"""FastAPI 依赖：身份解析、访问上下文与审计记录。

调用方通过 ``X-Access-Token`` 请求头表明身份；每个受保护端点都经
``get_access_context`` 拿到统一访问边界，并在允许/拒绝时写入审计。
审计只记录调用参数与判定结果，绝不写入统计数值等敏感内容。
"""

import json
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import AuditLog, UserAccount, UserCollegeGrant
from app.services.access_control import (
    AccessContext,
    AccessDeniedError,
    DENIED_MESSAGE,
    build_access_context,
)


def get_current_user(
    x_access_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> UserAccount:
    if not x_access_token:
        raise HTTPException(status_code=401, detail="未提供访问凭证")
    user = db.query(UserAccount).filter(UserAccount.api_token == x_access_token).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="访问凭证无效")
    return user


def get_access_context(
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccessContext:
    grants = db.query(UserCollegeGrant).filter(
        UserCollegeGrant.user_id == user.id
    ).all()
    return build_access_context(user, [g.college_id for g in grants])


def record_audit(
    db: Session,
    ctx: AccessContext,
    action: str,
    resource: str,
    params: Optional[dict] = None,
    decision: str = "allowed",
    reason: Optional[str] = None,
) -> None:
    """写入一条审计记录。params 只应包含调用方提供的筛选参数。"""

    entry = AuditLog(
        user_id=ctx.user_id,
        username=ctx.username,
        role=ctx.role.value if hasattr(ctx.role, "value") else str(ctx.role),
        action=action,
        resource=resource,
        params=json.dumps(params, ensure_ascii=False, default=str) if params else None,
        decision=decision,
        reason=reason,
    )
    db.add(entry)
    db.commit()


def deny_request(
    db: Session,
    ctx: AccessContext,
    action: str,
    resource: str,
    params: Optional[dict],
    error: AccessDeniedError,
) -> None:
    """统一处理越权：记录审计后抛出不含敏感信息的 403。"""

    record_audit(
        db, ctx, action, resource, params,
        decision="denied", reason=error.reason,
    )
    raise HTTPException(status_code=403, detail=DENIED_MESSAGE)


def require_school_wide(ctx: AccessContext) -> None:
    """要求调用方具备校级全量能力（如全量预警检测、基础数据维护）。"""

    if not ctx.is_school_wide:
        raise AccessDeniedError()
