from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core import get_db, get_access_context, deny_request, require_school_wide
from app.models import AuditLog
from app.schemas import AuditLog as AuditLogSchema
from app.services.access_control import AccessContext, AccessDeniedError

router = APIRouter(prefix="/audit-logs", tags=["访问审计"])


@router.get("", response_model=List[AuditLogSchema])
def list_audit_logs(
    user_id: Optional[int] = Query(None, description="调用方用户ID"),
    decision: Optional[str] = Query(None, description="判定：allowed/denied"),
    action: Optional[str] = Query(None, description="操作标识"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    try:
        require_school_wide(ctx)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "audit_logs.list", "/audit-logs", None, exc)

    query = db.query(AuditLog)
    if user_id is not None:
        query = query.filter(AuditLog.user_id == user_id)
    if decision:
        query = query.filter(AuditLog.decision == decision)
    if action:
        query = query.filter(AuditLog.action == action)

    return query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit).all()
