"""报告分享链接：只保存报告定义，访问时按当前权限实时校验并重新计算。

- 创建时校验创建者对报告参数的授权范围；
- 每次访问都重新校验链接未撤销、创建者当前权限仍覆盖报告参数、
  访问者当前权限也覆盖报告参数，任一不满足即明确拒绝；
- 报告内容实时计算，不写回任何缓存，权限收窄后旧链接立即失效。
"""

import secrets
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import get_db, get_access_context, record_audit, deny_request
from app.models import (
    College,
    Graduate,
    MicroMajor,
    ReportShareLink,
    UserAccount,
    UserCollegeGrant,
)
from app.schemas import (
    ReportItem,
    ReportResponse,
    ShareLink as ShareLinkSchema,
    ShareLinkCreate,
)
from app.services.access_control import (
    AccessContext,
    AccessDeniedError,
    assert_college_allowed,
    assert_micro_major_allowed,
    build_access_context,
    combine_scopes,
)
from app.utils import calculate_group_stats
from app.utils.stats_calculator import _eager_load_follow_ups

router = APIRouter(prefix="/share-links", tags=["报告分享"])

VALID_REPORT_TYPES = {"college", "micro_major", "year"}


def _context_for_user(db: Session, user: UserAccount) -> AccessContext:
    grants = db.query(UserCollegeGrant).filter(
        UserCollegeGrant.user_id == user.id
    ).all()
    return build_access_context(user, [g.college_id for g in grants])


def _validate_link_params(db: Session, ctx: AccessContext, link_in: ShareLinkCreate) -> None:
    """校验报告类型与参数组合，且参数在指定上下文的授权范围内。"""

    if link_in.report_type not in VALID_REPORT_TYPES:
        raise HTTPException(status_code=400, detail="不支持的报告类型")
    if link_in.report_type == "college" and link_in.college_id is None:
        raise HTTPException(status_code=400, detail="学院报告必须指定学院")
    if link_in.report_type == "micro_major" and link_in.micro_major_id is None:
        raise HTTPException(status_code=400, detail="微专业报告必须指定微专业")
    if link_in.report_type == "year" and link_in.graduation_year is None:
        raise HTTPException(status_code=400, detail="届次报告必须指定毕业届次")

    if link_in.college_id is not None:
        college = db.query(College).filter(College.id == link_in.college_id).first()
        if not college:
            raise HTTPException(status_code=404, detail="学院不存在")
        assert_college_allowed(ctx, link_in.college_id)
    if link_in.micro_major_id is not None:
        assert_micro_major_allowed(db, ctx, link_in.micro_major_id)


def _assert_link_accessible(db: Session, link: ReportShareLink, creator_ctx: AccessContext, requester_ctx: AccessContext) -> None:
    """每次访问都基于当前权限重新校验，任一环节失效即拒绝。"""

    if link.is_revoked:
        raise AccessDeniedError("链接已撤销")
    if link.college_id is not None:
        assert_college_allowed(creator_ctx, link.college_id)
        assert_college_allowed(requester_ctx, link.college_id)
    if link.micro_major_id is not None:
        assert_micro_major_allowed(db, creator_ctx, link.micro_major_id)
        assert_micro_major_allowed(db, requester_ctx, link.micro_major_id)


def _stats_item(dimension: str, value: str, graduates) -> ReportItem:
    stats = calculate_group_stats(graduates)
    return ReportItem(
        dimension=dimension,
        dimension_value=value,
        total_count=stats.total_count,
        confirmed_rate=stats.confirmed_rate,
        aligned_rate=stats.aligned_rate,
        avg_salary_display=stats.avg_salary_display,
        avg_satisfaction_display=stats.avg_satisfaction_display,
        retention_rate_display=stats.retention_rate_display,
        follow_up_count=stats.follow_up_count,
    )


def _generate_shared_report(
    db: Session,
    link: ReportShareLink,
    effective_scope,
) -> ReportResponse:
    """按链接定义实时计算报告内容，不写缓存。"""

    if link.report_type == "college":
        college = db.query(College).filter(College.id == link.college_id).first()
        if not college:
            raise HTTPException(status_code=404, detail="报告对象不存在")
        graduates = db.query(Graduate).filter(Graduate.college_id == link.college_id).all()
        _eager_load_follow_ups(db, graduates)
        item = _stats_item("学院", college.name, graduates)
        report_type = "学院复核报告"
    elif link.report_type == "micro_major":
        micro_major = db.query(MicroMajor).filter(MicroMajor.id == link.micro_major_id).first()
        if not micro_major:
            raise HTTPException(status_code=404, detail="报告对象不存在")
        graduates = db.query(Graduate).filter(
            Graduate.micro_major_id == link.micro_major_id,
            Graduate.has_micro_major == True,
        ).all()
        _eager_load_follow_ups(db, graduates)
        item = _stats_item("微专业", micro_major.name, graduates)
        report_type = "微专业复核报告"
    else:
        year_query = db.query(Graduate).filter(Graduate.graduation_year == link.graduation_year)
        if effective_scope is not None:
            year_query = year_query.filter(Graduate.college_id.in_(effective_scope))
        graduates = year_query.all()
        _eager_load_follow_ups(db, graduates)
        item = _stats_item("届次", f"{link.graduation_year}届", graduates)
        report_type = "届次复核报告"

    return ReportResponse(
        report_type=report_type,
        data=[item],
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


@router.post("", response_model=ShareLinkSchema)
def create_share_link(
    link_in: ShareLinkCreate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    try:
        _validate_link_params(db, ctx, link_in)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "share_links.create", "/share-links", link_in.model_dump(), exc)
    except LookupError:
        raise HTTPException(status_code=404, detail="微专业不存在")

    link = ReportShareLink(
        token=secrets.token_urlsafe(24),
        title=link_in.title,
        report_type=link_in.report_type,
        college_id=link_in.college_id,
        micro_major_id=link_in.micro_major_id,
        graduation_year=link_in.graduation_year,
        created_by=ctx.user_id,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    record_audit(db, ctx, "share_links.create", "/share-links", {"report_type": link.report_type, "link_id": link.id})
    return link


@router.get("", response_model=List[ShareLinkSchema])
def list_share_links(
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    query = db.query(ReportShareLink)
    if not ctx.is_school_wide:
        query = query.filter(ReportShareLink.created_by == ctx.user_id)
    return query.order_by(ReportShareLink.created_at.desc()).all()


@router.get("/{token}", response_model=ReportResponse)
def access_shared_report(
    token: str,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    link = db.query(ReportShareLink).filter(ReportShareLink.token == token).first()
    if not link:
        raise HTTPException(status_code=404, detail="分享链接不存在")

    # 审计只记录链接标识，不写入令牌本身
    resource = f"/share-links/#{link.id}"
    params = {"link_id": link.id, "report_type": link.report_type}
    creator = db.query(UserAccount).filter(UserAccount.id == link.created_by).first()
    if not creator or not creator.is_active:
        deny_request(db, ctx, "share_links.access", resource, params, AccessDeniedError("链接创建人已失效"))

    creator_ctx = _context_for_user(db, creator)
    try:
        _assert_link_accessible(db, link, creator_ctx, ctx)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "share_links.access", resource, params, exc)
    except LookupError:
        raise HTTPException(status_code=404, detail="报告对象不存在")

    effective_scope = combine_scopes(creator_ctx.allowed_college_ids, ctx.allowed_college_ids)
    record_audit(db, ctx, "share_links.access", resource, params)
    return _generate_shared_report(db, link, effective_scope)


@router.delete("/{link_id}")
def revoke_share_link(
    link_id: int,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    link = db.query(ReportShareLink).filter(ReportShareLink.id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="分享链接不存在")
    if not ctx.is_school_wide and link.created_by != ctx.user_id:
        deny_request(db, ctx, "share_links.revoke", f"/share-links/{link_id}", {"link_id": link_id}, AccessDeniedError("只能撤销本人创建的链接"))

    link.is_revoked = True
    db.commit()
    record_audit(db, ctx, "share_links.revoke", f"/share-links/{link_id}", {"link_id": link_id})
    return {"message": "链接已撤销"}
