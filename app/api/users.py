"""调用方账号与学院授权管理，仅校级可用。

授权记录保存在服务端，调整后立即生效：统计、预警、明细接口的下一次
请求按新范围执行，已生成的报告分享链接也会在访问时重新校验。
"""

import secrets
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import get_db, get_access_context, record_audit, deny_request, require_school_wide
from app.models import College, UserAccount, UserCollegeGrant
from app.schemas import UserAccount as UserAccountSchema
from app.schemas import UserAccountCreate, UserGrantUpdate
from app.services.access_control import AccessContext, AccessDeniedError

router = APIRouter(prefix="/users", tags=["账号与授权"])


def _require_admin(db: Session, ctx: AccessContext, action: str):
    try:
        require_school_wide(ctx)
    except AccessDeniedError as exc:
        deny_request(db, ctx, action, "/users", None, exc)


def _serialize(user: UserAccount) -> UserAccountSchema:
    return UserAccountSchema(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        college_ids=[g.college_id for g in user.college_grants],
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get("", response_model=List[UserAccountSchema])
def list_users(
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    _require_admin(db, ctx, "users.list")
    return [_serialize(u) for u in db.query(UserAccount).order_by(UserAccount.id).all()]


@router.post("", response_model=UserAccountSchema)
def create_user(
    user_in: UserAccountCreate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    _require_admin(db, ctx, "users.create")

    existing = db.query(UserAccount).filter(UserAccount.username == user_in.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="登录名已存在")

    user = UserAccount(
        username=user_in.username,
        display_name=user_in.display_name,
        role=user_in.role,
        api_token=user_in.api_token or secrets.token_urlsafe(24),
        is_active=user_in.is_active,
    )
    db.add(user)
    db.flush()

    for college_id in user_in.college_ids:
        college = db.query(College).filter(College.id == college_id).first()
        if not college:
            raise HTTPException(status_code=404, detail=f"学院不存在: {college_id}")
        db.add(UserCollegeGrant(user_id=user.id, college_id=college_id, granted_by=ctx.username))

    db.commit()
    db.refresh(user)
    record_audit(db, ctx, "users.create", "/users", {"username": user.username})
    return _serialize(user)


@router.put("/{user_id}/grants", response_model=UserAccountSchema)
def replace_grants(
    user_id: int,
    grant_in: UserGrantUpdate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    _require_admin(db, ctx, "users.replace_grants")

    user = db.query(UserAccount).filter(UserAccount.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    for college_id in grant_in.college_ids:
        college = db.query(College).filter(College.id == college_id).first()
        if not college:
            raise HTTPException(status_code=404, detail=f"学院不存在: {college_id}")

    db.query(UserCollegeGrant).filter(UserCollegeGrant.user_id == user.id).delete()
    for college_id in grant_in.college_ids:
        db.add(UserCollegeGrant(user_id=user.id, college_id=college_id, granted_by=ctx.username))

    db.commit()
    db.refresh(user)
    record_audit(db, ctx, "users.replace_grants", f"/users/{user_id}/grants", {"user_id": user_id})
    return _serialize(user)
