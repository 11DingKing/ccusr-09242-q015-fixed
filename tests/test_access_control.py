"""访问控制边界验收测试。

对比校级与学院角色在学院、微专业、年度查询上的可见范围，
确认跨范围参数被明确拒绝，报告分享链接随权限变化重新校验，
且响应与审计记录都不泄露敏感数字。
"""

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from main import app
from app.core import get_db
from app.models import (
    Base,
    AttributionRecord,
    College,
    DestinationStatus,
    DestinationType,
    Graduate,
    MicroMajor,
    UserAccount,
    UserCollegeGrant,
    UserRole,
    Warning,
    WarningLevel,
    WarningStatus,
    WarningType,
)

ADMIN = {"X-Access-Token": "admin-token"}
CS = {"X-Access-Token": "cs-token"}
ECON = {"X-Access-Token": "econ-token"}

CS_NAME = "计算机学院"
ECON_NAME = "经济学院"
AI_NAME = "人工智能"
FINTECH_NAME = "金融科技"


def _seed(db):
    cs = College(name=CS_NAME, code="CS")
    econ = College(name=ECON_NAME, code="ECON")
    db.add_all([cs, econ])
    db.flush()

    ai = MicroMajor(name=AI_NAME, code="M-AI", college_id=cs.id)
    fintech = MicroMajor(name=FINTECH_NAME, code="M-FT", college_id=econ.id)
    db.add_all([ai, fintech])
    db.flush()

    graduates = [
        # 计算机学院：2023 两名修读人工智能且已落实；2024 一名已落实一名待登记
        Graduate(student_id="s1", name="甲", major="软件工程", graduation_year=2023,
                 college_id=cs.id, has_micro_major=True, micro_major_id=ai.id,
                 destination_status=DestinationStatus.CONFIRMED, destination_type=DestinationType.EMPLOYMENT),
        Graduate(student_id="s2", name="乙", major="软件工程", graduation_year=2023,
                 college_id=cs.id, has_micro_major=True, micro_major_id=ai.id,
                 destination_status=DestinationStatus.VERIFIED, destination_type=DestinationType.EMPLOYMENT),
        Graduate(student_id="s3", name="丙", major="软件工程", graduation_year=2024,
                 college_id=cs.id, has_micro_major=False,
                 destination_status=DestinationStatus.CONFIRMED, destination_type=DestinationType.EMPLOYMENT),
        Graduate(student_id="s4", name="丁", major="软件工程", graduation_year=2024,
                 college_id=cs.id, has_micro_major=False,
                 destination_status=DestinationStatus.PENDING, destination_type=DestinationType.UNDECIDED),
        # 经济学院：2023 一名修读金融科技待登记；2024 一名已落实一名变动中
        Graduate(student_id="s5", name="戊", major="金融学", graduation_year=2023,
                 college_id=econ.id, has_micro_major=True, micro_major_id=fintech.id,
                 destination_status=DestinationStatus.PENDING, destination_type=DestinationType.UNDECIDED),
        Graduate(student_id="s6", name="己", major="金融学", graduation_year=2024,
                 college_id=econ.id, has_micro_major=False,
                 destination_status=DestinationStatus.CONFIRMED, destination_type=DestinationType.EMPLOYMENT),
        Graduate(student_id="s7", name="庚", major="金融学", graduation_year=2024,
                 college_id=econ.id, has_micro_major=False,
                 destination_status=DestinationStatus.CHANGING, destination_type=DestinationType.UNDECIDED),
    ]
    db.add_all(graduates)
    db.flush()

    warnings = [
        Warning(warning_type=WarningType.CONFIRMED_RATE_DECLINE, warning_level=WarningLevel.YELLOW,
                status=WarningStatus.ACTIVE, target_type="college", target_id=cs.id,
                target_name=CS_NAME, indicator="confirmed_rate", current_value=75.0,
                start_year=2023, end_year=2024, decline_count=1),
        Warning(warning_type=WarningType.ALIGNED_RATE_DECLINE, warning_level=WarningLevel.ORANGE,
                status=WarningStatus.ACTIVE, target_type="micro_major", target_id=fintech.id,
                target_name=FINTECH_NAME, indicator="aligned_rate", current_value=33.3,
                start_year=2023, end_year=2024, decline_count=1),
        Warning(warning_type=WarningType.BELOW_PROVINCE_LINE, warning_level=WarningLevel.RED,
                status=WarningStatus.RESOLVED, target_type="college", target_id=econ.id,
                target_name=ECON_NAME, indicator="confirmed_rate", current_value=33.33,
                start_year=2024, end_year=2024, decline_count=1),
    ]
    db.add_all(warnings)
    db.flush()

    db.add_all([
        AttributionRecord(warning_id=warnings[0].id, category="培养质量", description="计算机学院归因"),
        AttributionRecord(warning_id=warnings[1].id, category="行业遇冷", description="经济学院归因"),
    ])

    admin = UserAccount(username="admin", display_name="校级管理员",
                        role=UserRole.SCHOOL_ADMIN, api_token="admin-token")
    cs_user = UserAccount(username="cs_staff", display_name="计算机复核员",
                          role=UserRole.COLLEGE_STAFF, api_token="cs-token")
    econ_user = UserAccount(username="econ_staff", display_name="经济复核员",
                            role=UserRole.COLLEGE_STAFF, api_token="econ-token")
    db.add_all([admin, cs_user, econ_user])
    db.flush()
    db.add_all([
        UserCollegeGrant(user_id=cs_user.id, college_id=cs.id, granted_by="admin"),
        UserCollegeGrant(user_id=econ_user.id, college_id=econ.id, granted_by="admin"),
    ])
    db.commit()

    return {
        "cs_id": cs.id, "econ_id": econ.id,
        "ai_id": ai.id, "fintech_id": fintech.id,
        "cs_user_id": cs_user.id, "econ_user_id": econ_user.id,
        "warning_cs_id": warnings[0].id,
        "warning_econ_micro_id": warnings[1].id,
    }


class AccessControlTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        cls.Session = sessionmaker(bind=engine)
        db = cls.Session()
        cls.ids = _seed(db)
        db.close()

        def override_get_db():
            session = cls.Session()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()

    def setUp(self):
        # 清理审计并恢复授权，保证用例互不干扰
        db = self.Session()
        from app.models import AuditLog, ReportShareLink
        db.query(AuditLog).delete()
        db.query(ReportShareLink).delete()
        db.query(UserCollegeGrant).delete()
        db.add(UserCollegeGrant(user_id=self.ids["cs_user_id"], college_id=self.ids["cs_id"], granted_by="admin"))
        db.add(UserCollegeGrant(user_id=self.ids["econ_user_id"], college_id=self.ids["econ_id"], granted_by="admin"))
        # 清理用例间可能新增的毕业生
        db.query(Graduate).filter(Graduate.student_id == "s_new").delete()
        db.commit()
        db.close()

    # ---------- 基础认证 ----------

    def test_unauthenticated_requests_rejected(self):
        for path in ["/api/v1/statistics/comparison", "/api/v1/graduates", "/api/v1/warnings"]:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 401, path)
        resp = self.client.get("/api/v1/statistics/comparison", headers={"X-Access-Token": "bad"})
        self.assertEqual(resp.status_code, 401)

    # ---------- 学院维度报告对比 ----------

    def test_college_report_differs_by_role(self):
        admin_resp = self.client.get("/api/v1/statistics/reports/by-college", headers=ADMIN)
        self.assertEqual(admin_resp.status_code, 200)
        admin_rows = {r["dimension_value"]: r for r in admin_resp.json()["data"]}
        self.assertEqual(set(admin_rows), {CS_NAME, ECON_NAME})
        self.assertEqual(admin_rows[ECON_NAME]["total_count"], 3)

        cs_resp = self.client.get("/api/v1/statistics/reports/by-college", headers=CS)
        self.assertEqual(cs_resp.status_code, 200)
        cs_rows = cs_resp.json()["data"]
        self.assertEqual(len(cs_rows), 1)
        self.assertEqual(cs_rows[0]["dimension_value"], CS_NAME)
        self.assertEqual(cs_rows[0]["total_count"], 4)
        self.assertEqual(cs_rows[0]["confirmed_rate"], 75.0)
        # 不泄露其他学院名称与数字
        self.assertNotIn(ECON_NAME, cs_resp.text)
        self.assertNotIn("33.33", cs_resp.text)

    def test_year_report_scoped_to_authorized_college(self):
        admin_rows = {r["dimension_value"]: r for r in
                      self.client.get("/api/v1/statistics/reports/by-year", headers=ADMIN).json()["data"]}
        self.assertEqual(admin_rows["2023届"]["total_count"], 3)
        self.assertEqual(admin_rows["2024届"]["total_count"], 4)

        cs_rows = {r["dimension_value"]: r for r in
                   self.client.get("/api/v1/statistics/reports/by-year", headers=CS).json()["data"]}
        self.assertEqual(cs_rows["2023届"]["total_count"], 2)
        self.assertEqual(cs_rows["2023届"]["confirmed_rate"], 100.0)
        self.assertEqual(cs_rows["2024届"]["total_count"], 2)
        self.assertEqual(cs_rows["2024届"]["confirmed_rate"], 50.0)

    def test_micro_major_report_scoped(self):
        admin_rows = {r["dimension_value"]: r for r in
                      self.client.get("/api/v1/statistics/reports/by-micro-major", headers=ADMIN).json()["data"]}
        self.assertEqual(set(admin_rows), {"未修读微专业", AI_NAME, FINTECH_NAME})
        self.assertEqual(admin_rows["未修读微专业"]["total_count"], 4)

        cs_resp = self.client.get("/api/v1/statistics/reports/by-micro-major", headers=CS)
        cs_rows = {r["dimension_value"]: r for r in cs_resp.json()["data"]}
        self.assertEqual(set(cs_rows), {"未修读微专业", AI_NAME})
        self.assertEqual(cs_rows["未修读微专业"]["total_count"], 2)
        self.assertEqual(cs_rows[AI_NAME]["total_count"], 2)
        self.assertNotIn(FINTECH_NAME, cs_resp.text)

    # ---------- 组合筛选 ----------

    def test_comparison_cross_scope_params_denied(self):
        resp = self.client.get("/api/v1/statistics/comparison",
                               params={"college_id": self.ids["econ_id"]}, headers=CS)
        self.assertEqual(resp.status_code, 403)

        resp = self.client.get("/api/v1/statistics/comparison",
                               params={"micro_major_id": self.ids["fintech_id"]}, headers=CS)
        self.assertEqual(resp.status_code, 403)

        resp = self.client.get("/api/v1/statistics/follow-up-comparison",
                               params={"college_id": self.ids["econ_id"]}, headers=CS)
        self.assertEqual(resp.status_code, 403)

    def test_comparison_scoped_automatically(self):
        resp = self.client.get("/api/v1/statistics/comparison", headers=CS)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["with_micro"]["total_count"], 2)
        self.assertEqual(body["without_micro"]["total_count"], 2)

        admin_body = self.client.get("/api/v1/statistics/comparison", headers=ADMIN).json()
        self.assertEqual(admin_body["with_micro"]["total_count"], 3)
        self.assertEqual(admin_body["without_micro"]["total_count"], 4)

        # 学院角色显式指定本院，数字与校级按同学院过滤一致
        cs_explicit = self.client.get("/api/v1/statistics/comparison",
                                      params={"college_id": self.ids["cs_id"]}, headers=CS).json()
        admin_explicit = self.client.get("/api/v1/statistics/comparison",
                                         params={"college_id": self.ids["cs_id"]}, headers=ADMIN).json()
        self.assertEqual(cs_explicit, admin_explicit)

    def test_trend_scoped(self):
        resp = self.client.get(f"/api/v1/statistics/trend/{self.ids['fintech_id']}", headers=CS)
        self.assertEqual(resp.status_code, 403)

        cs_trend = {t["year"]: t for t in
                    self.client.get(f"/api/v1/statistics/trend/{self.ids['ai_id']}", headers=CS).json()["trend"]}
        self.assertEqual(cs_trend[2023]["with_micro_count"], 2)
        self.assertEqual(cs_trend[2024]["without_micro_count"], 2)

        admin_trend = {t["year"]: t for t in
                       self.client.get(f"/api/v1/statistics/trend/{self.ids['ai_id']}", headers=ADMIN).json()["trend"]}
        self.assertEqual(admin_trend[2024]["without_micro_count"], 4)
        # 2023 届全校学生均修读了微专业，未修读对照组为 0
        self.assertEqual(admin_trend[2023]["without_micro_count"], 0)

    # ---------- 预警 ----------

    def test_warnings_scoped(self):
        cs_body = self.client.get("/api/v1/warnings", headers=CS).json()
        self.assertEqual(cs_body["total"], 1)
        self.assertEqual(cs_body["active_count"], 1)
        self.assertEqual(cs_body["resolved_count"], 0)
        self.assertEqual(cs_body["data"][0]["target_name"], CS_NAME)

        admin_body = self.client.get("/api/v1/warnings", headers=ADMIN).json()
        self.assertEqual(admin_body["total"], 3)
        self.assertEqual(admin_body["active_count"], 2)
        self.assertEqual(admin_body["resolved_count"], 1)

    def test_warning_detail_cross_scope_denied(self):
        resp = self.client.get(f"/api/v1/warnings/{self.ids['warning_econ_micro_id']}", headers=CS)
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get("/api/v1/warnings",
                               params={"target_type": "college", "target_id": self.ids["econ_id"]}, headers=CS)
        self.assertEqual(resp.status_code, 403)

    def test_warning_detection_scoped(self):
        resp = self.client.post("/api/v1/warnings/detect", headers=CS)
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post("/api/v1/warnings/detect",
                                params={"target_type": "college", "target_id": self.ids["econ_id"]}, headers=CS)
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post("/api/v1/warnings/detect",
                                params={"target_type": "college", "target_id": self.ids["cs_id"]}, headers=CS)
        self.assertEqual(resp.status_code, 200)

    # ---------- 毕业生明细 ----------

    def test_graduates_scoped(self):
        cs_body = self.client.get("/api/v1/graduates", headers=CS).json()
        self.assertEqual(len(cs_body), 4)
        self.assertTrue(all(g["college_id"] == self.ids["cs_id"] for g in cs_body))

        admin_body = self.client.get("/api/v1/graduates", headers=ADMIN).json()
        self.assertEqual(len(admin_body), 7)

        resp = self.client.get("/api/v1/graduates", params={"college_id": self.ids["econ_id"]}, headers=CS)
        self.assertEqual(resp.status_code, 403)

        econ_grad_id = admin_body[-1]["id"] if admin_body[-1]["college_id"] == self.ids["econ_id"] else None
        econ_grad_id = next(g["id"] for g in admin_body if g["college_id"] == self.ids["econ_id"])
        resp = self.client.get(f"/api/v1/graduates/{econ_grad_id}", headers=CS)
        self.assertEqual(resp.status_code, 403)

    # ---------- 归因 ----------

    def test_attributions_scoped(self):
        cs_body = self.client.get("/api/v1/attributions", headers=CS).json()
        self.assertEqual(len(cs_body), 1)
        self.assertEqual(cs_body[0]["description"], "计算机学院归因")

        dist = self.client.get("/api/v1/attributions/distribution", headers=CS).json()
        self.assertEqual(dist["total_records"], 1)

        admin_dist = self.client.get("/api/v1/attributions/distribution", headers=ADMIN).json()
        self.assertEqual(admin_dist["total_records"], 2)

    # ---------- 报告分享链接 ----------

    def _create_link(self, headers, **payload):
        resp = self.client.post("/api/v1/share-links", headers=headers, json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_share_link_access_and_scope(self):
        link = self._create_link(ADMIN, title="计算机学院报告", report_type="college",
                                 college_id=self.ids["cs_id"])

        # 授权学院用户可访问，内容实时计算
        resp = self.client.get(f"/api/v1/share-links/{link['token']}", headers=CS)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"][0]["total_count"], 4)

        # 其他学院用户访问同一链接被拒绝
        resp = self.client.get(f"/api/v1/share-links/{link['token']}", headers=ECON)
        self.assertEqual(resp.status_code, 403)

        # 学院用户不能创建越权链接
        resp = self.client.post("/api/v1/share-links", headers=CS,
                                json={"title": "越权", "report_type": "college",
                                      "college_id": self.ids["econ_id"]})
        self.assertEqual(resp.status_code, 403)

    def test_share_link_content_not_cached(self):
        link = self._create_link(ADMIN, title="计算机学院报告", report_type="college",
                                 college_id=self.ids["cs_id"])
        before = self.client.get(f"/api/v1/share-links/{link['token']}", headers=CS).json()
        self.assertEqual(before["data"][0]["total_count"], 4)

        # 新增一名计算机学院毕业生后，同一链接返回实时结果
        resp = self.client.post("/api/v1/graduates", headers=ADMIN, json={
            "student_id": "s_new", "name": "新", "major": "软件工程",
            "graduation_year": 2024, "college_id": self.ids["cs_id"],
        })
        self.assertEqual(resp.status_code, 200)
        after = self.client.get(f"/api/v1/share-links/{link['token']}", headers=CS).json()
        self.assertEqual(after["data"][0]["total_count"], 5)

    def test_share_link_invalidated_by_permission_change(self):
        link = self._create_link(CS, title="本院报告", report_type="college",
                                 college_id=self.ids["cs_id"])
        token = link["token"]
        self.assertEqual(self.client.get(f"/api/v1/share-links/{token}", headers=CS).status_code, 200)

        # 校级收回该学院授权后，旧链接对创建者与校级访问者都立即失效
        resp = self.client.put(f"/api/v1/users/{self.ids['cs_user_id']}/grants",
                               headers=ADMIN, json={"college_ids": []})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/share-links/{token}", headers=CS).status_code, 403)
        self.assertEqual(self.client.get(f"/api/v1/share-links/{token}", headers=ADMIN).status_code, 403)

        # 恢复授权后链接重新可用
        self.client.put(f"/api/v1/users/{self.ids['cs_user_id']}/grants",
                        headers=ADMIN, json={"college_ids": [self.ids["cs_id"]]})
        self.assertEqual(self.client.get(f"/api/v1/share-links/{token}", headers=CS).status_code, 200)

        # 撤销后再次失效
        resp = self.client.delete(f"/api/v1/share-links/{link['id']}", headers=CS)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/share-links/{token}", headers=CS).status_code, 403)

    # ---------- 审计与响应不泄露 ----------

    def test_denied_response_and_audit_do_not_leak(self):
        resp = self.client.get("/api/v1/statistics/comparison",
                               params={"college_id": self.ids["econ_id"]}, headers=CS)
        self.assertEqual(resp.status_code, 403)
        # 响应不包含其他学院的名称或统计数字
        self.assertNotIn(ECON_NAME, resp.text)
        self.assertNotIn("33.33", resp.text)
        self.assertNotIn(FINTECH_NAME, resp.text)

        # 越权请求已进入审计，但审计内容同样不含敏感数字与他院名称
        logs = self.client.get("/api/v1/audit-logs", params={"decision": "denied"}, headers=ADMIN).json()
        self.assertTrue(any(l["action"] == "statistics.comparison" for l in logs))
        denied_text = str(logs)
        self.assertNotIn("33.33", denied_text)
        self.assertNotIn(ECON_NAME, denied_text)
        self.assertNotIn(FINTECH_NAME, denied_text)

    def test_audit_logs_school_only(self):
        resp = self.client.get("/api/v1/audit-logs", headers=CS)
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get("/api/v1/users", headers=CS)
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
