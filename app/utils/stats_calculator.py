from typing import List, Tuple, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models import Graduate, EmployerFollowUp, DestinationStatus, DestinationType
from app.schemas import GroupStats, ComparisonStats, FollowUpComparisonStats
from .salary_utils import get_salary_midpoint, format_salary_display


def calculate_group_stats(graduates: List[Graduate]) -> GroupStats:
    total_count = len(graduates)
    if total_count == 0:
        return GroupStats(
            total_count=0,
            confirmed_count=0,
            confirmed_rate=0.0,
            aligned_count=0,
            aligned_rate=0.0,
            avg_salary=None,
            avg_salary_display="暂无数据",
            avg_satisfaction=None,
            avg_satisfaction_display="暂无数据",
            retention_rate=None,
            retention_rate_display="暂无数据",
            follow_up_count=0,
        )

    confirmed_graduates = [
        g for g in graduates
        if g.destination_status in (DestinationStatus.CONFIRMED, DestinationStatus.VERIFIED)
    ]
    confirmed_count = len(confirmed_graduates)
    confirmed_rate = round((confirmed_count / total_count) * 100, 2)

    employed_graduates = [
        g for g in graduates
        if g.destination_type == DestinationType.EMPLOYMENT
    ]
    employed_count = len(employed_graduates)

    aligned_count = sum(1 for g in employed_graduates if g.is_aligned)
    aligned_rate = round((aligned_count / employed_count) * 100, 2) if employed_count > 0 else 0.0

    salaries = [
        get_salary_midpoint(g.salary_range)
        for g in graduates
        if g.salary_range is not None
    ]
    if salaries:
        avg_salary = round(sum(salaries) / len(salaries), 1)
    else:
        avg_salary = None

    follow_ups = []
    for g in graduates:
        if hasattr(g, 'follow_ups') and g.follow_ups:
            follow_ups.extend(g.follow_ups)

    satisfaction_scores = [
        fu.satisfaction_score for fu in follow_ups
        if fu.satisfaction_score is not None
    ]
    avg_satisfaction = round(sum(satisfaction_scores) / len(satisfaction_scores), 2) if satisfaction_scores else None

    graduates_with_fu = set(fu.graduate_id for fu in follow_ups)
    latest_fu_map = {}
    for fu in follow_ups:
        gid = fu.graduate_id
        if gid not in latest_fu_map or fu.follow_up_date > latest_fu_map[gid].follow_up_date:
            latest_fu_map[gid] = fu

    if latest_fu_map:
        still_employed = sum(1 for fu in latest_fu_map.values() if fu.is_still_employed)
        retention_rate = round((still_employed / len(latest_fu_map)) * 100, 2)
    else:
        retention_rate = None

    return GroupStats(
        total_count=total_count,
        confirmed_count=confirmed_count,
        confirmed_rate=confirmed_rate,
        aligned_count=aligned_count,
        aligned_rate=aligned_rate,
        avg_salary=avg_salary,
        avg_salary_display=format_salary_display(avg_salary),
        avg_satisfaction=avg_satisfaction,
        avg_satisfaction_display=_format_satisfaction(avg_satisfaction),
        retention_rate=retention_rate,
        retention_rate_display=_format_retention(retention_rate),
        follow_up_count=len(follow_ups),
    )


def _scoped_graduates(
    db: Session,
    scope,
    graduation_year: Optional[int] = None,
    college_id: Optional[int] = None,
) -> List[Graduate]:
    """按调用方授权范围取毕业生：学院角色在此被强制叠加学院过滤。

    显式的 college_id / micro_major_id 合法性由 API 层先行校验（越权即 403），
    这里再次以范围条件收口，保证任何调用路径都不会绕出授权学院。
    """
    query = scope.graduate_base_query(db)

    filters = []
    if graduation_year:
        filters.append(Graduate.graduation_year == graduation_year)
    if college_id:
        filters.append(Graduate.college_id == college_id)

    if filters:
        query = query.filter(and_(*filters))

    graduates = query.all()
    _eager_load_follow_ups(db, graduates)
    return graduates


def _split_micro_groups(all_graduates, micro_major_id: Optional[int]):
    if micro_major_id:
        with_micro = [
            g for g in all_graduates
            if g.has_micro_major and g.micro_major_id == micro_major_id
        ]
        without_micro = [
            g for g in all_graduates
            if not g.has_micro_major
        ]
    else:
        with_micro = [g for g in all_graduates if g.has_micro_major]
        without_micro = [g for g in all_graduates if not g.has_micro_major]
    return with_micro, without_micro


def get_comparison_stats(
    db: Session,
    graduation_year: int = None,
    college_id: int = None,
    micro_major_id: int = None,
    scope=None,
) -> ComparisonStats:
    if scope is None:
        from app.core.security import AccessScope
        scope = AccessScope.school()

    all_graduates = _scoped_graduates(db, scope, graduation_year, college_id)

    with_micro, without_micro = _split_micro_groups(all_graduates, micro_major_id)

    return ComparisonStats(
        with_micro=calculate_group_stats(with_micro),
        without_micro=calculate_group_stats(without_micro)
    )


def get_follow_up_comparison(
    db: Session,
    graduation_year: int = None,
    college_id: int = None,
    micro_major_id: int = None,
    scope=None,
) -> FollowUpComparisonStats:
    if scope is None:
        from app.core.security import AccessScope
        scope = AccessScope.school()

    all_graduates = _scoped_graduates(db, scope, graduation_year, college_id)

    with_micro, without_micro = _split_micro_groups(all_graduates, micro_major_id)

    return FollowUpComparisonStats(
        with_micro=calculate_group_stats(with_micro),
        without_micro=calculate_group_stats(without_micro),
    )


def _eager_load_follow_ups(db: Session, graduates: List[Graduate]):
    if not graduates:
        return
    gids = [g.id for g in graduates]
    all_follow_ups = db.query(EmployerFollowUp).filter(
        EmployerFollowUp.graduate_id.in_(gids)
    ).all()

    fu_by_grad = {}
    for fu in all_follow_ups:
        fu_by_grad.setdefault(fu.graduate_id, []).append(fu)

    for g in graduates:
        g.follow_ups = fu_by_grad.get(g.id, [])


def _format_satisfaction(avg: float = None) -> str:
    if avg is None:
        return "暂无数据"
    return f"{avg:.1f}/5"


def _format_retention(rate: float = None) -> str:
    if rate is None:
        return "暂无数据"
    return f"{rate:.1f}%"
