import sys
import os
import random
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import SessionLocal, init_db
from app.models import (
    College,
    MicroMajor,
    Graduate,
    StatusChangeLog,
    EmployerFollowUp,
    ProvinceReferenceLine,
    DestinationStatus,
    DestinationType,
    SalaryRange,
    SalaryChange,
    INDUSTRIES,
    UserAccount,
    UserCollegeGrant,
    UserRole,
)


def init_colleges(db):
    colleges = [
        {"name": "计算机科学与技术学院", "code": "CS"},
        {"name": "经济学院", "code": "ECON"},
        {"name": "机械工程学院", "code": "ME"},
        {"name": "电子信息工程学院", "code": "EE"},
        {"name": "管理学院", "code": "MGT"},
    ]
    for c in colleges:
        if not db.query(College).filter(College.code == c["code"]).first():
            db.add(College(**c))
    db.commit()
    return {c.code: c for c in db.query(College).all()}


def init_micro_majors(db, colleges):
    micro_majors = [
        {"name": "人工智能", "code": "MICRO-AI", "college_code": "CS", "description": "机器学习、深度学习、计算机视觉等前沿技术"},
        {"name": "金融科技", "code": "MICRO-FINTECH", "college_code": "ECON", "description": "区块链、量化金融、金融大数据分析"},
        {"name": "智能制造", "code": "MICRO-IM", "college_code": "ME", "description": "工业4.0、智能工厂、机器人应用"},
        {"name": "大数据分析", "code": "MICRO-BIGDATA", "college_code": "CS", "description": "数据挖掘、统计分析、可视化技术"},
        {"name": "数字营销", "code": "MICRO-DM", "college_code": "MGT", "description": "新媒体运营、用户增长、数据分析"},
    ]
    for mm in micro_majors:
        if not db.query(MicroMajor).filter(MicroMajor.code == mm["code"]).first():
            college = db.query(College).filter(College.code == mm["college_code"]).first()
            db.add(MicroMajor(
                name=mm["name"],
                code=mm["code"],
                description=mm["description"],
                college_id=college.id
            ))
    db.commit()
    return {mm.code: mm for mm in db.query(MicroMajor).all()}


def generate_graduates(db, colleges, micro_majors):
    first_names = ["张", "王", "李", "刘", "陈", "杨", "黄", "赵", "周", "吴", "徐", "孙", "朱", "马", "胡", "郭", "何", "高", "林", "罗"]
    last_names = ["伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "军", "洋", "勇", "艳", "杰", "娟", "涛", "明", "超", "秀英", "霞", "平"]

    majors = {
        "CS": ["计算机科学与技术", "软件工程", "网络空间安全"],
        "ECON": ["经济学", "金融学", "国际经济与贸易"],
        "ME": ["机械设计制造及其自动化", "车辆工程", "工业工程"],
        "EE": ["电子信息工程", "通信工程", "自动化"],
        "MGT": ["工商管理", "会计学", "人力资源管理"],
    }

    years = [2022, 2023, 2024, 2025]
    total_count = 1800

    micro_major_list = list(micro_majors.values())
    college_list = list(colleges.values())

    year_trend_factors = {
        2022: {"confirmed_bonus": 0.08, "aligned_bonus": 0.10},
        2023: {"confirmed_bonus": 0.05, "aligned_bonus": 0.06},
        2024: {"confirmed_bonus": 0.02, "aligned_bonus": 0.02},
        2025: {"confirmed_bonus": -0.03, "aligned_bonus": -0.05},
    }

    micro_major_decline = {
        "MICRO-AI": {"confirmed": -0.15, "aligned": -0.18},
        "MICRO-IM": {"confirmed": -0.12, "aligned": -0.10},
    }

    graduates = []

    for i in range(total_count):
        year = random.choice(years)
        college = random.choice(college_list)
        major_list = majors.get(college.code, ["其他专业"])
        major = random.choice(major_list)

        has_micro = random.random() < 0.35
        micro_major = random.choice(micro_major_list) if has_micro else None

        name = random.choice(first_names) + random.choice(last_names)
        gender = random.choice(["男", "女"])
        student_id = f"{year}{college.code}{i:04d}"

        trend_factor = year_trend_factors.get(year, {})
        confirmed_bonus = trend_factor.get("confirmed_bonus", 0)
        aligned_bonus = trend_factor.get("aligned_bonus", 0)

        mm_confirmed_decline = 0
        mm_aligned_decline = 0
        if micro_major and micro_major.code in micro_major_decline:
            if year >= 2023:
                mm_confirmed_decline = micro_major_decline[micro_major.code]["confirmed"] * (year - 2022) / 3
                mm_aligned_decline = micro_major_decline[micro_major.code]["aligned"] * (year - 2022) / 3

        if has_micro:
            base_confirmed = 0.85 + confirmed_bonus + mm_confirmed_decline
            base_aligned = 0.78 + aligned_bonus + mm_aligned_decline
            confirmed_rate = max(0.5, min(0.95, base_confirmed))
            aligned_rate = max(0.4, min(0.9, base_aligned))

            pending_prob = max(0.02, 0.10 - confirmed_bonus)
            confirmed_prob = confirmed_rate * 0.5
            verified_prob = confirmed_rate * 0.5
            changing_prob = max(0.05, 0.10 - confirmed_bonus / 2)

            status_weights = [pending_prob, confirmed_prob, changing_prob, verified_prob]
            type_weights = [0.32, 0.58, 0.10]
            aligned_prob = aligned_rate
            salary_weights = [0.08, 0.18, 0.28, 0.30, 0.16]
        else:
            base_confirmed = 0.70 + confirmed_bonus + mm_confirmed_decline
            base_aligned = 0.55 + aligned_bonus + mm_aligned_decline
            confirmed_rate = max(0.4, min(0.85, base_confirmed))
            aligned_rate = max(0.3, min(0.75, base_aligned))

            pending_prob = max(0.08, 0.15 - confirmed_bonus)
            confirmed_prob = confirmed_rate * 0.5
            verified_prob = confirmed_rate * 0.5
            changing_prob = max(0.08, 0.12 - confirmed_bonus / 2)

            status_weights = [pending_prob, confirmed_prob, changing_prob, verified_prob]
            type_weights = [0.25, 0.52, 0.23]
            aligned_prob = aligned_rate
            salary_weights = [0.15, 0.28, 0.30, 0.20, 0.07]

        status = random.choices(
            [DestinationStatus.PENDING, DestinationStatus.CONFIRMED,
             DestinationStatus.CHANGING, DestinationStatus.VERIFIED],
            weights=status_weights, k=1
        )[0]

        dest_type = random.choices(
            [DestinationType.FURTHER_STUDY, DestinationType.EMPLOYMENT, DestinationType.UNDECIDED],
            weights=type_weights, k=1
        )[0]

        if status in (DestinationStatus.CONFIRMED, DestinationStatus.VERIFIED) and dest_type == DestinationType.EMPLOYMENT:
            salary_range = random.choices(
                [SalaryRange.BELOW_6, SalaryRange.RANGE_6_8, SalaryRange.RANGE_8_10,
                 SalaryRange.RANGE_10_15, SalaryRange.ABOVE_15],
                weights=salary_weights, k=1
            )[0]
            unit_industry = random.choice(INDUSTRIES)
            is_aligned = random.random() < aligned_prob
        else:
            salary_range = None
            unit_industry = None
            is_aligned = False

        graduate = Graduate(
            student_id=student_id,
            name=name,
            gender=gender,
            major=major,
            graduation_year=year,
            college_id=college.id,
            has_micro_major=has_micro,
            micro_major_id=micro_major.id if micro_major else None,
            destination_status=status,
            destination_type=dest_type,
            unit_industry=unit_industry,
            salary_range=salary_range,
            is_aligned=is_aligned,
        )
        graduates.append(graduate)

    db.bulk_save_objects(graduates)
    db.commit()

    all_graduates = db.query(Graduate).all()
    logs = []
    for g in all_graduates:
        logs.append(StatusChangeLog(
            graduate_id=g.id,
            old_status=None,
            new_status=g.destination_status,
            changed_by="system",
            remark="初始数据导入",
            changed_at=datetime.now()
        ))

    db.bulk_save_objects(logs)
    db.commit()

    return len(graduates)


def generate_follow_ups(db):
    employed_graduates = db.query(Graduate).filter(
        Graduate.destination_type == DestinationType.EMPLOYMENT,
        Graduate.destination_status.in_([DestinationStatus.CONFIRMED, DestinationStatus.VERIFIED])
    ).all()

    if not employed_graduates:
        return 0

    employer_names = [
        "华为技术有限公司", "阿里巴巴集团", "腾讯科技", "字节跳动",
        "百度在线", "京东集团", "美团点评", "拼多多",
        "网易公司", "小米科技", "比亚迪股份", "中国工商银行",
        "中国建设银行", "招商银行", "中国平安", "中兴通讯",
        "大疆创新", "海尔集团", "格力电器", "中国中车",
    ]

    job_titles = [
        "软件工程师", "数据分析师", "产品经理", "算法工程师",
        "前端开发工程师", "后端开发工程师", "测试工程师", "运维工程师",
        "金融分析师", "市场营销专员", "人力资源专员", "财务分析师",
        "机械设计工程师", "电气工程师", "项目经理", "咨询顾问",
    ]

    visitors = ["张老师", "李老师", "王老师", "刘老师", "陈老师"]

    follow_ups = []
    for g in employed_graduates:
        num_follow_ups = random.choices([0, 1, 2, 3], weights=[0.35, 0.35, 0.20, 0.10], k=1)[0]

        if g.has_micro_major:
            base_satisfaction = random.uniform(3.5, 5.0)
            still_employed_prob = 0.88
            aligned_prob = 0.80
        else:
            base_satisfaction = random.uniform(2.8, 4.5)
            still_employed_prob = 0.75
            aligned_prob = 0.55

        grad_date = date(g.graduation_year, 7, 1)

        for fu_idx in range(num_follow_ups):
            months_after = (fu_idx + 1) * 6
            fu_date = grad_date + timedelta(days=months_after * 30)

            if fu_date > date.today():
                continue

            satisfaction = round(min(5.0, max(1.0, base_satisfaction + random.uniform(-0.5, 0.5))), 1)
            is_still_employed = random.random() < still_employed_prob

            if fu_idx == 0:
                salary_change = SalaryChange.UNCHANGED
            else:
                if g.has_micro_major:
                    salary_change = random.choices(
                        [SalaryChange.DECREASED, SalaryChange.UNCHANGED,
                         SalaryChange.INCREASED_SMALL, SalaryChange.INCREASED_LARGE],
                        weights=[0.05, 0.30, 0.40, 0.25], k=1
                    )[0]
                else:
                    salary_change = random.choices(
                        [SalaryChange.DECREASED, SalaryChange.UNCHANGED,
                         SalaryChange.INCREASED_SMALL, SalaryChange.INCREASED_LARGE],
                        weights=[0.10, 0.40, 0.35, 0.15], k=1
                    )[0]

            is_aligned = random.random() < aligned_prob

            follow_ups.append(EmployerFollowUp(
                graduate_id=g.id,
                follow_up_date=fu_date,
                is_aligned=is_aligned,
                satisfaction_score=satisfaction,
                is_still_employed=is_still_employed,
                salary_change=salary_change,
                employer_name=random.choice(employer_names) if fu_idx == 0 else None,
                job_title=random.choice(job_titles) if fu_idx == 0 else None,
                remark=None,
                visited_by=random.choice(visitors),
            ))

    if follow_ups:
        db.bulk_save_objects(follow_ups)
        db.commit()

    return len(follow_ups)


def init_province_reference_lines(db):
    reference_lines = [
        {"graduation_year": 2022, "indicator": "confirmed_rate", "province_average": 80.0, "threshold": 75.0},
        {"graduation_year": 2022, "indicator": "aligned_rate", "province_average": 60.0, "threshold": 55.0},
        {"graduation_year": 2023, "indicator": "confirmed_rate", "province_average": 79.0, "threshold": 74.0},
        {"graduation_year": 2023, "indicator": "aligned_rate", "province_average": 59.0, "threshold": 54.0},
        {"graduation_year": 2024, "indicator": "confirmed_rate", "province_average": 78.0, "threshold": 73.0},
        {"graduation_year": 2024, "indicator": "aligned_rate", "province_average": 58.0, "threshold": 53.0},
        {"graduation_year": 2025, "indicator": "confirmed_rate", "province_average": 77.0, "threshold": 72.0},
        {"graduation_year": 2025, "indicator": "aligned_rate", "province_average": 57.0, "threshold": 52.0},
    ]

    count = 0
    for b in reference_lines:
        existing = db.query(ProvinceReferenceLine).filter(
            ProvinceReferenceLine.graduation_year == b["graduation_year"],
            ProvinceReferenceLine.indicator == b["indicator"],
        ).first()
        if not existing:
            db.add(ProvinceReferenceLine(**b))
            count += 1
    db.commit()
    return count


def init_users(db, colleges):
    """初始化演示账号：一个校级管理员 + 每个学院一个复核账号。

    演示环境使用固定令牌，调用接口时通过 X-Access-Token 请求头携带。
    """
    if not db.query(UserAccount).filter(UserAccount.username == "school_admin").first():
        db.add(UserAccount(
            username="school_admin",
            display_name="校级管理员",
            role=UserRole.SCHOOL_ADMIN,
            api_token="school-admin-token",
        ))
    db.commit()

    created = 0
    for code, college in colleges.items():
        username = f"college_{code.lower()}"
        user = db.query(UserAccount).filter(UserAccount.username == username).first()
        if not user:
            user = UserAccount(
                username=username,
                display_name=f"{college.name}复核员",
                role=UserRole.COLLEGE_STAFF,
                api_token=f"college-{code.lower()}-token",
            )
            db.add(user)
            db.flush()
            created += 1
        has_grant = db.query(UserCollegeGrant).filter(
            UserCollegeGrant.user_id == user.id,
            UserCollegeGrant.college_id == college.id,
        ).first()
        if not has_grant:
            db.add(UserCollegeGrant(
                user_id=user.id,
                college_id=college.id,
                granted_by="school_admin",
            ))
    db.commit()
    return created


def main():
    print("=" * 60)
    print("正在初始化数据库...")
    init_db()

    db = SessionLocal()
    try:
        print("\n1. 正在初始化学院数据...")
        colleges = init_colleges(db)
        print(f"   已加载 {len(colleges)} 个学院")

        print("\n2. 正在初始化微专业数据...")
        micro_majors = init_micro_majors(db, colleges)
        print(f"   已加载 {len(micro_majors)} 个微专业")

        print("\n3. 正在生成毕业生示例数据...")
        count = generate_graduates(db, colleges, micro_majors)
        print(f"   已生成 {count} 条毕业生数据")

        for year in [2022, 2023, 2024, 2025]:
            year_count = db.query(Graduate).filter(Graduate.graduation_year == year).count()
            print(f"   {year}届: {year_count} 人")

        with_micro = db.query(Graduate).filter(Graduate.has_micro_major == True).count()
        without_micro = db.query(Graduate).filter(Graduate.has_micro_major == False).count()
        print(f"   修读微专业: {with_micro} 人")
        print(f"   未修读微专业: {without_micro} 人")

        print("\n4. 正在生成用人单位回访示例数据...")
        fu_count = generate_follow_ups(db)
        employed = db.query(Graduate).filter(
            Graduate.destination_type == DestinationType.EMPLOYMENT
        ).count()
        fu_graduates = db.query(EmployerFollowUp.graduate_id).distinct().count()
        print(f"   已就业毕业生: {employed} 人")
        print(f"   已回访毕业生: {fu_graduates} 人")
        print(f"   回访记录总数: {fu_count} 条")

        print("\n5. 正在初始化省基准线数据...")
        bench_count = init_province_reference_lines(db)
        print(f"   已初始化 {bench_count} 条省基准线数据")

        print("\n6. 正在初始化演示账号与学院授权...")
        user_count = init_users(db, colleges)
        print(f"   校级管理员: school_admin (令牌 school-admin-token)")
        print(f"   新创建学院账号: {user_count} 个 (令牌形如 college-cs-token)")

        print("\n" + "=" * 60)
        print("数据初始化完成！")
        print("=" * 60)

    except Exception as e:
        print(f"初始化失败: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
