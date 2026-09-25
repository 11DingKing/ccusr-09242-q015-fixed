"""访问控制边界验收测试：对比校级/学院角色在学院、微专业、年度查询上的差异，
并确认越权请求被明确拒绝、报告链接随权限重新校验、响应与审计均不泄露敏感数字。
"""

import base64
import json
import os
import tempfile
import unittest
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import get_db
from app.core.security import audit_log
from app.core.security.deps import _sign
from app.core.config import settings
from app.models import (
    Base,
    College,
    MicroMajor,
    Graduate,
    EmployerFollowUp,
    Warning,
    AttributionRecord,
    DestinationStatus,
    DestinationType,
    WarningType,
    WarningLevel,
    WarningStatus,
    AttributionCategory,
)
import main as main_module

SCHOOL_HEADERS = {"X-Auth-Role": "school"}
COLLEGE1_HEADERS = {"X-Auth-Role": "college", "X-Auth-College-Id": "1"}
COLLEGE2_HEADERS = {"X-Auth-Role": "college", "X-Auth-College-Id": "2"}

SENSITIVE_KEYS = (
    "total_count", "confirmed_rate", "aligned_rate", "avg_salary",
    "satisfaction", "retention", "with_micro", "without_micro",
    "current_value", "province_value",
)


class AccessControlAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        cls.engine = create_engine(
            f"sqlite:///{cls.db_path}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=cls.engine)
        cls.SessionLocal = sessionmaker(bind=cls.engine, autocommit=False, autoflush=False)
        cls._seed()

        def override_get_db():
            db = cls.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main_module.app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(main_module.app)

    @classmethod
    def tearDownClass(cls):
        main_module.app.dependency_overrides.clear()
        cls.engine.dispose()
        os.remove(cls.db_path)

    def setUp(self):
        audit_log.clear()

    @classmethod
    def _seed(cls):
        db = cls.SessionLocal()
        c1 = College(name="计算机学院", code="C1")
        c2 = College(name="经济学院", code="C2")
        db.add_all([c1, c2])
        db.flush()
        m1 = MicroMajor(name="人工智能", code="M1", college_id=c1.id)
        m2 = MicroMajor(name="金融科技", code="M2", college_id=c2.id)
        db.add_all([m1, m2])
        db.flush()

        def grad(student_id, year, college, mm, status, dtype=DestinationType.EMPLOYMENT):
            return Graduate(
                student_id=student_id, name=student_id, major="X",
                graduation_year=year, college_id=college,
                has_micro_major=mm is not None, micro_major_id=mm,
                destination_status=status, destination_type=dtype,
                is_aligned=True,
            )

        confirmed = DestinationStatus.CONFIRMED
        pending = DestinationStatus.PENDING
        graduates = [
            # 学院1：2024、2025 两届，共 4 人（2 人修读本学院微专业）
            grad("g1", 2024, c1.id, m1.id, confirmed),
            grad("g2", 2024, c1.id, None, confirmed),
            grad("g3", 2025, c1.id, m1.id, confirmed),
            grad("g4", 2025, c1.id, None, pending),
            # 学院2：仅 2023 届，共 2 人
            grad("g5", 2023, c2.id, m2.id, confirmed),
            grad("g6", 2023, c2.id, None, confirmed),
        ]
        db.add_all(graduates)
        db.flush()
        db.add_all([
            EmployerFollowUp(graduate_id=graduates[0].id, follow_up_date=date(2025, 1, 1)),
            EmployerFollowUp(graduate_id=graduates[4].id, follow_up_date=date(2024, 1, 1)),
        ])

        w1 = Warning(
            warning_type=WarningType.CONFIRMED_RATE_DECLINE,
            warning_level=WarningLevel.YELLOW, status=WarningStatus.ACTIVE,
            target_type="college", target_id=c1.id, target_name="计算机学院",
            indicator="confirmed_rate", current_value=11.11, start_year=2024, end_year=2025,
            decline_details="[]",
        )
        w2 = Warning(
            warning_type=WarningType.BELOW_PROVINCE_LINE,
            warning_level=WarningLevel.RED, status=WarningStatus.ACTIVE,
            target_type="micro_major", target_id=m2.id, target_name="金融科技",
            indicator="confirmed_rate", current_value=22.22, province_value=77.0, gap=55.0,
            start_year=2023, end_year=2023, decline_details="[]",
        )
        db.add_all([w1, w2])
        db.flush()
        db.add_all([
            AttributionRecord(
                warning_id=w1.id, category=AttributionCategory.OTHER,
                description="学院1敏感归因数字11.11", analyst="a",
            ),
            AttributionRecord(
                warning_id=w2.id, category=AttributionCategory.OTHER,
                description="学院2敏感归因数字22.22", analyst="b",
            ),
        ])
        db.commit()
        cls.c1_id, cls.c2_id = c1.id, c2.id
        cls.m1_id, cls.m2_id = m1.id, m2.id
        cls.w1_id, cls.w2_id = w1.id, w2.id
        db.close()

    # ------------------------------------------------------------------ 认证

    def test_missing_role_is_rejected(self):
        r = self.client.get("/api/v1/statistics/comparison")
        self.assertEqual(r.status_code, 401)

    def test_college_role_without_college_header_is_rejected(self):
        r = self.client.get(
            "/api/v1/statistics/comparison", headers={"X-Auth-Role": "college"}
        )
        self.assertEqual(r.status_code, 401)

    def test_college_role_bound_to_unknown_college_is_rejected(self):
        r = self.client.get(
            "/api/v1/statistics/comparison",
            headers={"X-Auth-Role": "college", "X-Auth-College-Id": "999"},
        )
        self.assertEqual(r.status_code, 403)

    # ------------------------------------------------------ 组合筛选：统计接口

    def test_school_comparison_sees_all_colleges(self):
        r = self.client.get("/api/v1/statistics/comparison", headers=SCHOOL_HEADERS)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        total = body["with_micro"]["total_count"] + body["without_micro"]["total_count"]
        self.assertEqual(total, 6)

    def test_college_comparison_is_scoped_without_college_param(self):
        r = self.client.get("/api/v1/statistics/comparison", headers=COLLEGE1_HEADERS)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        total = body["with_micro"]["total_count"] + body["without_micro"]["total_count"]
        # 不带学院参数时，学院角色拿到的是本学院，而非全校 6 人。
        self.assertEqual(total, 4)
        self.assertEqual(body["with_micro"]["total_count"], 2)

    def test_college_explicit_own_college_and_micro_major_allowed(self):
        r = self.client.get(
            f"/api/v1/statistics/comparison?college_id={self.c1_id}",
            headers=COLLEGE1_HEADERS,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.json()["with_micro"]["total_count"]
            + r.json()["without_micro"]["total_count"],
            4,
        )
        r = self.client.get(
            f"/api/v1/statistics/comparison?micro_major_id={self.m1_id}",
            headers=COLLEGE1_HEADERS,
        )
        self.assertEqual(r.status_code, 200)

    def test_college_cross_college_param_is_explicitly_denied(self):
        r = self.client.get(
            f"/api/v1/statistics/comparison?college_id={self.c2_id}",
            headers=COLLEGE1_HEADERS,
        )
        self.assertEqual(r.status_code, 403)
        # 越权响应不得携带任何数字（不是 total_count=0 的空数据）。
        self.assertNotRegex(r.text, r"\d")

    def test_college_cross_micro_major_param_is_denied_on_all_stats(self):
        for path in (
            f"/api/v1/statistics/comparison?micro_major_id={self.m2_id}",
            f"/api/v1/statistics/follow-up-comparison?micro_major_id={self.m2_id}",
            f"/api/v1/statistics/trend/{self.m2_id}",
        ):
            r = self.client.get(path, headers=COLLEGE1_HEADERS)
            self.assertEqual(r.status_code, 403, path)
            self.assertNotRegex(r.text, r"\d", path)

    def test_year_filter_is_scoped_to_college(self):
        # 学院1只有 2024/2025 届；学院2的 2023 届数据必须不可见。
        r = self.client.get(
            "/api/v1/statistics/comparison?graduation_year=2023",
            headers=COLLEGE1_HEADERS,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(
            body["with_micro"]["total_count"] + body["without_micro"]["total_count"],
            0,
        )
        school = self.client.get(
            "/api/v1/statistics/comparison?graduation_year=2023",
            headers=SCHOOL_HEADERS,
        ).json()
        self.assertEqual(
            school["with_micro"]["total_count"] + school["without_micro"]["total_count"],
            2,
        )

    # ------------------------------------------------------------- 趋势查询

    def test_trend_scoped_years_and_control_group(self):
        r = self.client.get(
            f"/api/v1/statistics/trend/{self.m1_id}", headers=COLLEGE1_HEADERS
        )
        self.assertEqual(r.status_code, 200)
        years = [item["year"] for item in r.json()["trend"]]
        self.assertEqual(set(years), {2024, 2025})
        for item in r.json()["trend"]:
            # 未修读对照组只能统计本学院学生
            self.assertLessEqual(item["without_micro_count"], 2)

    def test_other_college_student_in_micro_major_not_counted(self):
        # 学院2 的学生修读学院1 的微专业：学院1 的画像/趋势不得把其计入。
        db = self.SessionLocal()
        intruder = Graduate(
            student_id="g7", name="g7", major="X", graduation_year=2025,
            college_id=self.c2_id, has_micro_major=True, micro_major_id=self.m1_id,
            destination_status=DestinationStatus.CONFIRMED,
            destination_type=DestinationType.EMPLOYMENT, is_aligned=True,
        )
        db.add(intruder)
        db.commit()
        db.close()
        try:
            profile = self.client.get(
                f"/api/v1/micro-majors/{self.m1_id}/profile",
                headers=COLLEGE1_HEADERS,
            ).json()
            # 本学院修读 m1 的只有 2 人，混入的外学院学生不得抬高总数。
            self.assertEqual(profile["stats"]["overall"]["total_count"], 2)

            trend = self.client.get(
                f"/api/v1/statistics/trend/{self.m1_id}", headers=COLLEGE1_HEADERS
            ).json()
            item_2025 = next(t for t in trend["trend"] if t["year"] == 2025)
            self.assertEqual(item_2025["with_micro_count"], 1)
        finally:
            db = self.SessionLocal()
            db.query(Graduate).filter(Graduate.student_id == "g7").delete()
            db.commit()
            db.close()

    # ------------------------------------------------------------- 报告接口

    def test_report_by_college_differs_by_role(self):
        school = self.client.get(
            "/api/v1/statistics/reports/by-college", headers=SCHOOL_HEADERS
        ).json()
        college = self.client.get(
            "/api/v1/statistics/reports/by-college", headers=COLLEGE1_HEADERS
        ).json()
        self.assertEqual(len(school["data"]), 2)
        self.assertEqual(len(college["data"]), 1)
        self.assertEqual(college["data"][0]["dimension_value"], "计算机学院")
        self.assertEqual(college["data"][0]["total_count"], 4)

    def test_report_by_micro_major_scoped(self):
        college = self.client.get(
            "/api/v1/statistics/reports/by-micro-major", headers=COLLEGE1_HEADERS
        ).json()
        names = [row["dimension_value"] for row in college["data"]]
        self.assertIn("人工智能", names)
        self.assertNotIn("金融科技", names)
        # “未修读”对照组也是本学院口径（2 人），不是全校 3 人。
        no_micro = next(row for row in college["data"] if row["dimension_value"] == "未修读微专业")
        self.assertEqual(no_micro["total_count"], 2)

    def test_report_by_year_scoped(self):
        college = self.client.get(
            "/api/v1/statistics/reports/by-year", headers=COLLEGE1_HEADERS
        ).json()
        years = {row["dimension_value"] for row in college["data"]}
        self.assertEqual(years, {"2024届", "2025届"})

    def test_statistics_responses_are_not_cacheable(self):
        r = self.client.get(
            "/api/v1/statistics/reports/by-college", headers=COLLEGE1_HEADERS
        )
        self.assertEqual(r.headers.get("Cache-Control"), "no-store")

    # ------------------------------------------------------- 预警与归因为明细

    def test_warning_list_and_detail_scoped(self):
        body = self.client.get("/api/v1/warnings", headers=COLLEGE1_HEADERS).json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["active_count"], 1)
        self.assertEqual(body["data"][0]["target_id"], self.c1_id)

        self.assertEqual(
            self.client.get(
                f"/api/v1/warnings/{self.w2_id}", headers=COLLEGE1_HEADERS
            ).status_code,
            403,
        )

    def test_warning_report_cross_target_is_denied_not_emptied(self):
        r = self.client.get(
            "/api/v1/statistics/reports/warnings"
            f"?target_type=micro_major&target_id={self.m2_id}",
            headers=COLLEGE1_HEADERS,
        )
        self.assertEqual(r.status_code, 403)

    def test_warning_target_type_only_filter_is_allowed_and_scoped(self):
        r = self.client.get(
            "/api/v1/statistics/reports/warnings?target_type=college",
            headers=COLLEGE1_HEADERS,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["total"], 1)
        self.assertEqual(r.json()["data"][0]["target_type"], "college")

    def test_warning_target_id_only_cross_scope_denied(self):
        r = self.client.get(
            f"/api/v1/statistics/reports/warnings?target_id={self.m2_id}",
            headers=COLLEGE1_HEADERS,
        )
        self.assertEqual(r.status_code, 403)

    def test_invalid_target_type_is_400(self):
        r = self.client.get(
            "/api/v1/statistics/reports/warnings?target_type=graduate",
            headers=COLLEGE1_HEADERS,
        )
        self.assertEqual(r.status_code, 400)

    def test_attribution_distribution_scoped(self):
        school = self.client.get(
            "/api/v1/statistics/reports/attribution-distribution",
            headers=SCHOOL_HEADERS,
        ).json()
        college = self.client.get(
            "/api/v1/statistics/reports/attribution-distribution",
            headers=COLLEGE1_HEADERS,
        ).json()
        self.assertEqual(school["total_records"], 2)
        self.assertEqual(college["total_records"], 1)
        # top_targets 不得出现其他学院的微专业名称
        names = [t["target_name"] for t in college["top_targets"]]
        self.assertNotIn("金融科技", names)

    def test_attribution_list_warning_filter_denies_cross_scope(self):
        self.assertEqual(
            self.client.get(
                "/api/v1/attributions/2", headers=COLLEGE1_HEADERS
            ).status_code,
            403,
        )
        listing = self.client.get(
            f"/api/v1/attributions?warning_id={self.w2_id}",
            headers=COLLEGE1_HEADERS,
        )
        self.assertEqual(listing.status_code, 403)

    # ------------------------------------------------------------- 明细接口

    def test_graduate_endpoints_scoped(self):
        self.assertEqual(
            self.client.get("/api/v1/graduates/5", headers=COLLEGE1_HEADERS).status_code,
            403,
        )
        body = self.client.get("/api/v1/graduates", headers=COLLEGE1_HEADERS).json()
        self.assertEqual({g["student_id"] for g in body}, {"g1", "g2", "g3", "g4"})
        cross = self.client.get(
            f"/api/v1/graduates?college_id={self.c2_id}", headers=COLLEGE1_HEADERS
        )
        self.assertEqual(cross.status_code, 403)

    def test_follow_ups_scoped(self):
        body = self.client.get("/api/v1/follow-ups", headers=COLLEGE1_HEADERS).json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["graduate_id"], 1)
        # 学院2学生的时间线不可访问
        self.assertEqual(
            self.client.get(
                "/api/v1/follow-ups/graduate/5/timeline", headers=COLLEGE1_HEADERS
            ).status_code,
            403,
        )

    def test_write_operations_denied_for_college_role(self):
        self.assertEqual(
            self.client.post(
                "/api/v1/warnings/detect", headers=COLLEGE1_HEADERS
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/colleges",
                headers=COLLEGE1_HEADERS,
                json={"name": "X", "code": "X9"},
            ).status_code,
            403,
        )

    # ------------------------------------------------------- 报告链接重校验

    def _mint(self, headers, report_type="by-college"):
        r = self.client.get(
            f"/api/v1/statistics/reports/link?report_type={report_type}",
            headers=headers,
        )
        self.assertEqual(r.status_code, 200)
        return r.json()["link"]

    def test_report_link_revalidates_role_and_scope(self):
        school_link = self._mint(SCHOOL_HEADERS)
        # 学院老师持校级链接访问：必须重新校验后拒绝，不能回放校级缓存（2 行数据）。
        stolen = self.client.get(
            f"/api/v1/statistics/reports/by-college?link={school_link}",
            headers=COLLEGE1_HEADERS,
        )
        self.assertEqual(stolen.status_code, 403)
        self.assertNotRegex(stolen.text, r"\d")

        own_link = self._mint(COLLEGE1_HEADERS)
        ok = self.client.get(
            f"/api/v1/statistics/reports/by-college?link={own_link}",
            headers=COLLEGE1_HEADERS,
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(len(ok.json()["data"]), 1)

        # 链接绑定学院1，权限变为学院2 后必须拒绝。
        cross = self.client.get(
            f"/api/v1/statistics/reports/by-college?link={own_link}",
            headers=COLLEGE2_HEADERS,
        )
        self.assertEqual(cross.status_code, 403)

        # 篡改签名拒绝。
        tampered = own_link[:-2] + ("aa" if own_link[-2:] != "aa" else "bb")
        self.assertEqual(
            self.client.get(
                f"/api/v1/statistics/reports/by-college?link={tampered}",
                headers=COLLEGE1_HEADERS,
            ).status_code,
            403,
        )

    def test_expired_report_link_is_rejected(self):
        payload = {
            "v": 1, "report": "by-college", "role": "college",
            "colleges": [self.c1_id], "iat": 0, "exp": 1,
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        payload_b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        token = f"{payload_b64}.{_sign(payload_b64, settings.REPORT_LINK_SECRET)}"
        r = self.client.get(
            f"/api/v1/statistics/reports/by-college?link={token}",
            headers=COLLEGE1_HEADERS,
        )
        self.assertEqual(r.status_code, 403)

    # ------------------------------------------------------------- 审计安全

    def test_audit_records_contain_no_sensitive_numbers(self):
        paths = [
            f"/api/v1/statistics/comparison?college_id={self.c2_id}",
            f"/api/v1/statistics/trend/{self.m2_id}",
            f"/api/v1/warnings/{self.w2_id}",
            "/api/v1/graduates/5",
        ]
        for path in paths:
            self.client.get(path, headers=COLLEGE1_HEADERS)

        entries = audit_log.entries()
        self.assertEqual(len(entries), len(paths))
        reasons = {e.reason for e in entries}
        self.assertIn("college_out_of_scope", reasons)
        self.assertIn("micro_major_out_of_scope", reasons)

        serialized = json.dumps([e.to_dict() for e in entries], ensure_ascii=False)
        for key in SENSITIVE_KEYS:
            self.assertNotIn(key, serialized)
        # 审计中只允许出现资源ID与学院ID这类定位信息，统计样本数字不得入审计。
        self.assertNotIn("11.11", serialized)
        self.assertNotIn("22.22", serialized)


if __name__ == "__main__":
    unittest.main()
