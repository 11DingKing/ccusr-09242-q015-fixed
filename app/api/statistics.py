from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from collections import Counter

from app.core import get_db
from app.core.security import (
    AccessScope,
    require_read_scope,
    require_school_scope,
    enforce_query_filters,
    ensure_micro_major_accessible,
    ensure_target_accessible,
    accessible_warning_ids,
    mint_report_link,
    verify_report_link,
)
from app.models import (
    Graduate,
    College,
    MicroMajor,
    DestinationStatus,
    DestinationType,
    Warning,
    WarningStatus,
    AttributionRecord,
    AttributionCategory,
)
from app.schemas import (
    ComparisonStats,
    FollowUpComparisonStats,
    YearlyTrendResponse,
    YearlyTrendItem,
    ReportResponse,
    ReportItem,
    WarningListResponse,
    WarningListItem,
    AttributionDistributionResponse,
    AttributionDistributionItem,
)
from app.utils import (
    get_comparison_stats,
    get_follow_up_comparison,
    calculate_group_stats,
    run_warning_detection_for_target,
    calculate_yearly_indicators,
)
from app.utils.stats_calculator import _eager_load_follow_ups

router = APIRouter(prefix="/statistics", tags=["统计分析"])

REPORT_BY_COLLEGE = "by-college"
REPORT_BY_MICRO_MAJOR = "by-micro-major"
REPORT_BY_YEAR = "by-year"
REPORT_BY_EMPLOYER_FOLLOW_UP = "by-employer-follow-up"
REPORT_WARNINGS = "warnings"
REPORT_ATTRIBUTION_DISTRIBUTION = "attribution-distribution"


def _check_link(link: Optional[str], scope: AccessScope, report_type: str) -> None:
    """携带已生成的报告链接访问时，按调用方当前权限重新校验。"""
    if link:
        verify_report_link(link, scope, report_type)


@router.get("/comparison", response_model=ComparisonStats)
def get_group_comparison(
    graduation_year: Optional[int] = Query(None, description="毕业届次"),
    college_id: Optional[int] = Query(None, description="学院ID"),
    micro_major_id: Optional[int] = Query(None, description="微专业ID"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    # 显式传入的学院/微专业参数越权即 403，不返回易误解的空数据。
    enforce_query_filters(
        scope, db, college_id=college_id, micro_major_id=micro_major_id
    )
    return get_comparison_stats(
        db=db,
        graduation_year=graduation_year,
        college_id=college_id,
        micro_major_id=micro_major_id,
        scope=scope,
    )


@router.get("/follow-up-comparison", response_model=FollowUpComparisonStats)
def get_follow_up_group_comparison(
    graduation_year: Optional[int] = Query(None, description="毕业届次"),
    college_id: Optional[int] = Query(None, description="学院ID"),
    micro_major_id: Optional[int] = Query(None, description="微专业ID"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    enforce_query_filters(
        scope, db, college_id=college_id, micro_major_id=micro_major_id
    )
    return get_follow_up_comparison(
        db=db,
        graduation_year=graduation_year,
        college_id=college_id,
        micro_major_id=micro_major_id,
        scope=scope,
    )


@router.get("/trend/{micro_major_id}", response_model=YearlyTrendResponse)
def get_yearly_trend(
    micro_major_id: int,
    run_detection: bool = Query(False, description="是否先运行预警检测"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    # 趋势接口按路径参数定位微专业，越权直接拒绝。
    micro_major = ensure_micro_major_accessible(scope, db, micro_major_id)

    if run_detection:
        require_school_scope(scope)
        run_warning_detection_for_target(db, "micro_major", micro_major_id)

    active_warnings = scope.apply_warning_filter(
        db.query(Warning).filter(
            Warning.target_type == "micro_major",
            Warning.target_id == micro_major_id,
            Warning.status == WarningStatus.ACTIVE,
        ),
        db,
    ).all()

    # 届次清单与对照组（未修读）都限定在调用方范围内，
    # 学院角色拿不到其他学院的届次规模与对照率。
    years = [
        row[0]
        for row in scope.graduate_base_query(db)
        .with_entities(Graduate.graduation_year)
        .distinct()
        .order_by(Graduate.graduation_year)
        .all()
    ]

    yearly_indicators = calculate_yearly_indicators(
        db, "micro_major", micro_major_id, scope=scope
    )
    indicator_map = {d["year"]: d for d in yearly_indicators}

    trend = []
    for year in years:
        all_graduates = scope.graduate_base_query(db).filter(
            Graduate.graduation_year == year
        ).all()

        with_micro = [
            g for g in all_graduates
            if g.has_micro_major and g.micro_major_id == micro_major_id
        ]
        without_micro = [
            g for g in all_graduates
            if not g.has_micro_major
        ]

        with_micro_count = len(with_micro)
        without_micro_count = len(without_micro)

        def calc_confirmed_rate(grads):
            if not grads:
                return 0.0
            confirmed = [
                g for g in grads
                if g.destination_status in (DestinationStatus.CONFIRMED, DestinationStatus.VERIFIED)
            ]
            return round((len(confirmed) / len(grads)) * 100, 2)

        year_warnings = [
            w for w in active_warnings
            if w.start_year <= year <= w.end_year
        ]
        has_warning = len(year_warnings) > 0
        warning_types = [w.warning_type.value for w in year_warnings]

        indicator = indicator_map.get(year, {})

        trend.append(YearlyTrendItem(
            year=year,
            with_micro_rate=calc_confirmed_rate(with_micro),
            without_micro_rate=calc_confirmed_rate(without_micro),
            with_micro_count=with_micro_count,
            without_micro_count=without_micro_count,
            has_warning=has_warning,
            warning_types=warning_types,
            confirmed_rate=indicator.get("confirmed_rate", 0.0),
            aligned_rate=indicator.get("aligned_rate", 0.0),
        ))

    return YearlyTrendResponse(
        micro_major_name=micro_major.name,
        trend=trend,
        has_active_warnings=len(active_warnings) > 0,
        active_warning_count=len(active_warnings),
    )


@router.get("/reports/link")
def create_report_link(
    report_type: str = Query(..., description="报告类型"),
    ttl_seconds: int = Query(7 * 24 * 3600, ge=60, le=30 * 24 * 3600),
    scope: AccessScope = Depends(require_read_scope),
):
    """生成绑定调用方当前角色与学院范围的签名报告链接。"""
    token = mint_report_link(report_type, scope, ttl_seconds=ttl_seconds)
    return {
        "report_type": report_type,
        "link": token,
        "ttl_seconds": ttl_seconds,
    }


@router.get("/reports/by-college", response_model=ReportResponse)
def get_report_by_college(
    link: Optional[str] = Query(None, description="已生成的签名报告链接"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    _check_link(link, scope, REPORT_BY_COLLEGE)

    colleges_query = db.query(College)
    if scope.is_college:
        colleges_query = colleges_query.filter(College.id.in_(tuple(scope.college_ids)))
    colleges = colleges_query.order_by(College.id).all()
    data = []

    for college in colleges:
        graduates = scope.graduate_base_query(db).filter(
            Graduate.college_id == college.id
        ).all()
        _eager_load_follow_ups(db, graduates)

        stats = calculate_group_stats(graduates)
        data.append(ReportItem(
            dimension="学院",
            dimension_value=college.name,
            total_count=stats.total_count,
            confirmed_rate=stats.confirmed_rate,
            aligned_rate=stats.aligned_rate,
            avg_salary_display=stats.avg_salary_display,
            avg_satisfaction_display=stats.avg_satisfaction_display,
            retention_rate_display=stats.retention_rate_display,
            follow_up_count=stats.follow_up_count,
        ))

    return ReportResponse(
        report_type="按学院统计",
        data=data,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


@router.get("/reports/by-micro-major", response_model=ReportResponse)
def get_report_by_micro_major(
    link: Optional[str] = Query(None, description="已生成的签名报告链接"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    _check_link(link, scope, REPORT_BY_MICRO_MAJOR)

    mm_query = db.query(MicroMajor)
    if scope.is_college:
        mm_query = mm_query.filter(MicroMajor.college_id.in_(tuple(scope.college_ids)))
    micro_majors = mm_query.order_by(MicroMajor.id).all()
    data = []

    # “未修读微专业”对照组同样限定在授权范围内，
    # 学院角色看到的是本学院对照组，而非全校汇总。
    all_without_micro = scope.graduate_base_query(db).filter(
        Graduate.has_micro_major == False
    ).all()
    _eager_load_follow_ups(db, all_without_micro)
    stats_all = calculate_group_stats(all_without_micro)
    data.append(ReportItem(
        dimension="微专业",
        dimension_value="未修读微专业",
        total_count=stats_all.total_count,
        confirmed_rate=stats_all.confirmed_rate,
        aligned_rate=stats_all.aligned_rate,
        avg_salary_display=stats_all.avg_salary_display,
        avg_satisfaction_display=stats_all.avg_satisfaction_display,
        retention_rate_display=stats_all.retention_rate_display,
        follow_up_count=stats_all.follow_up_count,
    ))

    for mm in micro_majors:
        graduates = scope.graduate_base_query(db).filter(
            Graduate.micro_major_id == mm.id,
            Graduate.has_micro_major == True
        ).all()
        _eager_load_follow_ups(db, graduates)

        stats = calculate_group_stats(graduates)
        data.append(ReportItem(
            dimension="微专业",
            dimension_value=mm.name,
            total_count=stats.total_count,
            confirmed_rate=stats.confirmed_rate,
            aligned_rate=stats.aligned_rate,
            avg_salary_display=stats.avg_salary_display,
            avg_satisfaction_display=stats.avg_satisfaction_display,
            retention_rate_display=stats.retention_rate_display,
            follow_up_count=stats.follow_up_count,
        ))

    return ReportResponse(
        report_type="按微专业统计",
        data=data,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


@router.get("/reports/by-year", response_model=ReportResponse)
def get_report_by_year(
    link: Optional[str] = Query(None, description="已生成的签名报告链接"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    _check_link(link, scope, REPORT_BY_YEAR)

    years = [
        row[0]
        for row in scope.graduate_base_query(db)
        .with_entities(Graduate.graduation_year)
        .distinct()
        .order_by(Graduate.graduation_year)
        .all()
    ]
    data = []

    for year in years:
        graduates = scope.graduate_base_query(db).filter(
            Graduate.graduation_year == year
        ).all()
        _eager_load_follow_ups(db, graduates)

        stats = calculate_group_stats(graduates)
        data.append(ReportItem(
            dimension="届次",
            dimension_value=f"{year}届",
            total_count=stats.total_count,
            confirmed_rate=stats.confirmed_rate,
            aligned_rate=stats.aligned_rate,
            avg_salary_display=stats.avg_salary_display,
            avg_satisfaction_display=stats.avg_satisfaction_display,
            retention_rate_display=stats.retention_rate_display,
            follow_up_count=stats.follow_up_count,
        ))

    return ReportResponse(
        report_type="按届次统计",
        data=data,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


@router.get("/reports/by-employer-follow-up", response_model=ReportResponse)
def get_report_by_employer_follow_up(
    link: Optional[str] = Query(None, description="已生成的签名报告链接"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    _check_link(link, scope, REPORT_BY_EMPLOYER_FOLLOW_UP)

    employed_graduates = scope.graduate_base_query(db).filter(
        Graduate.destination_type == DestinationType.EMPLOYMENT
    ).all()
    _eager_load_follow_ups(db, employed_graduates)

    with_micro = [g for g in employed_graduates if g.has_micro_major]
    without_micro = [g for g in employed_graduates if not g.has_micro_major]

    stats_with = calculate_group_stats(with_micro)
    stats_without = calculate_group_stats(without_micro)

    data = [
        ReportItem(
            dimension="用人单位回访",
            dimension_value="修读微专业",
            total_count=stats_with.total_count,
            confirmed_rate=stats_with.confirmed_rate,
            aligned_rate=stats_with.aligned_rate,
            avg_salary_display=stats_with.avg_salary_display,
            avg_satisfaction_display=stats_with.avg_satisfaction_display,
            retention_rate_display=stats_with.retention_rate_display,
            follow_up_count=stats_with.follow_up_count,
        ),
        ReportItem(
            dimension="用人单位回访",
            dimension_value="未修读微专业",
            total_count=stats_without.total_count,
            confirmed_rate=stats_without.confirmed_rate,
            aligned_rate=stats_without.aligned_rate,
            avg_salary_display=stats_without.avg_salary_display,
            avg_satisfaction_display=stats_without.avg_satisfaction_display,
            retention_rate_display=stats_without.retention_rate_display,
            follow_up_count=stats_without.follow_up_count,
        ),
    ]

    return ReportResponse(
        report_type="用人单位回访对照统计",
        data=data,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


@router.get("/reports/warnings", response_model=WarningListResponse)
def get_warning_report(
    status: Optional[str] = Query(None, description="预警状态：预警中/已解决/已忽略"),
    warning_level: Optional[str] = Query(None, description="预警级别"),
    target_type: Optional[str] = Query(None, description="预警对象类型：micro_major/college"),
    target_id: Optional[int] = Query(None, description="预警对象ID"),
    run_detection: bool = Query(False, description="是否先运行全量预警检测"),
    link: Optional[str] = Query(None, description="已生成的签名报告链接"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    _check_link(link, scope, REPORT_WARNINGS)

    if run_detection:
        require_school_scope(scope)
        from app.utils import run_full_warning_detection
        run_full_warning_detection(db)

    # target_type/target_id 越权即 403，而不是被下面的范围过滤悄悄清空。
    ensure_target_accessible(scope, db, target_type, target_id)

    query = db.query(Warning)

    if status:
        query = query.filter(Warning.status == status)
    if warning_level:
        query = query.filter(Warning.warning_level == warning_level)
    if target_type:
        query = query.filter(Warning.target_type == target_type)
    if target_id:
        query = query.filter(Warning.target_id == target_id)

    # 学院角色强制只看本学院（含所属微专业）的预警。
    query = scope.apply_warning_filter(query, db)

    warnings = query.order_by(
        Warning.warning_level.desc(),
        Warning.created_at.desc(),
    ).all()

    count_query = scope.apply_warning_filter(db.query(Warning), db)
    active_count = count_query.filter(Warning.status == WarningStatus.ACTIVE).count()
    resolved_count = count_query.filter(Warning.status == WarningStatus.RESOLVED).count()

    data = []
    for w in warnings:
        attribution_count = db.query(AttributionRecord).filter(
            AttributionRecord.warning_id == w.id
        ).count()
        data.append(WarningListItem(
            id=w.id,
            warning_type=w.warning_type.value,
            warning_level=w.warning_level.value,
            status=w.status.value,
            target_type=w.target_type,
            target_id=w.target_id,
            target_name=w.target_name,
            indicator=w.indicator,
            current_value=w.current_value,
            province_value=w.province_value,
            gap=w.gap,
            start_year=w.start_year,
            end_year=w.end_year,
            decline_count=w.decline_count,
            description=w.description,
            attribution_count=attribution_count,
            created_at=w.created_at,
        ))

    return WarningListResponse(
        total=len(warnings),
        active_count=active_count,
        resolved_count=resolved_count,
        data=data,
    )


@router.get("/reports/attribution-distribution", response_model=AttributionDistributionResponse)
def get_attribution_distribution_report(
    target_type: Optional[str] = Query(None, description="对象类型：micro_major/college"),
    target_id: Optional[int] = Query(None, description="对象ID"),
    link: Optional[str] = Query(None, description="已生成的签名报告链接"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    _check_link(link, scope, REPORT_ATTRIBUTION_DISTRIBUTION)

    ensure_target_accessible(scope, db, target_type, target_id)

    # 归因记录经预警归属间接越权，必须先收敛到可访问的预警集合。
    allowed_warning_ids = accessible_warning_ids(scope, db)
    if not allowed_warning_ids:
        query = db.query(AttributionRecord).filter(False)
    else:
        query = db.query(AttributionRecord).filter(
            AttributionRecord.warning_id.in_(allowed_warning_ids)
        )

    if target_type or target_id:
        warning_query = db.query(Warning.id).filter(Warning.id.in_(allowed_warning_ids or [0]))
        if target_type:
            warning_query = warning_query.filter(Warning.target_type == target_type)
        if target_id:
            warning_query = warning_query.filter(Warning.target_id == target_id)
        warning_ids = [w[0] for w in warning_query.all()]
        if warning_ids:
            query = query.filter(AttributionRecord.warning_id.in_(warning_ids))
        else:
            query = query.filter(False)

    all_records = query.all()
    total_records = len(all_records)

    category_counter = Counter()
    examples_by_category = {}
    for record in all_records:
        cat_value = record.category.value if hasattr(record.category, 'value') else str(record.category)
        category_counter[cat_value] += 1
        if cat_value not in examples_by_category:
            examples_by_category[cat_value] = []
        if len(examples_by_category[cat_value]) < 3:
            warning = db.query(Warning).filter(Warning.id == record.warning_id).first()
            examples_by_category[cat_value].append({
                "record_id": record.id,
                "target_name": warning.target_name if warning else "未知",
                "target_type": warning.target_type if warning else "unknown",
                "description": record.description[:100] + "..." if len(record.description) > 100 else record.description,
            })

    distribution = []
    for cat in AttributionCategory:
        count = category_counter.get(cat.value, 0)
        percentage = round((count / total_records * 100), 2) if total_records > 0 else 0
        distribution.append(AttributionDistributionItem(
            category=cat.value,
            count=count,
            percentage=percentage,
            examples=examples_by_category.get(cat.value, []),
        ))

    target_counter = Counter()
    for record in all_records:
        warning = db.query(Warning).filter(Warning.id == record.warning_id).first()
        if warning:
            key = f"{warning.target_type}:{warning.target_id}:{warning.target_name}"
            target_counter[key] += 1

    top_targets = []
    for key, count in target_counter.most_common(5):
        t_type, t_id, t_name = key.split(":", 2)
        top_targets.append({
            "target_type": t_type,
            "target_id": int(t_id),
            "target_name": t_name,
            "attribution_count": count,
        })

    return AttributionDistributionResponse(
        total_records=total_records,
        distribution=distribution,
        top_targets=top_targets,
    )
