"""访问控制 FastAPI 依赖与报告链接重校验。

所有受保护接口统一通过 :func:`require_read_scope` 解析调用方范围；学院角色的
范围参数由 :func:`enforce_query_filters` 等助手强制校验，越权时抛出
:class:`ScopeViolation`，由全局异常处理器记录审计并转换为不含任何数字的 403。
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core import get_db, settings
from app.models import College
from .roles import Role, ROLE_HEADER, COLLEGE_HEADER, WRITE_ROLES
from .access_scope import (
    AccessScope,
    ScopeViolation,
    require_college,
    require_micro_major,
    require_graduate,
    require_target,
    require_warning,
)
from .audit import audit_log

#: 报告链接默认有效期（秒）
LINK_TTL_SECONDS = 7 * 24 * 3600


def _build_scope(
    request: Request,
    db: Session,
    role_header: Optional[str],
    college_header: Optional[str],
) -> AccessScope:
    role = Role.parse(role_header)
    if role is None:
        raise HTTPException(status_code=401, detail="缺少有效的调用方角色")

    if role is Role.SCHOOL:
        scope = AccessScope.school()
    else:
        raw = (college_header or "").strip()
        if not raw:
            raise HTTPException(status_code=401, detail="学院角色缺少学院标识")
        try:
            college_id = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=401, detail="学院标识无效")
        if college_id <= 0:
            raise HTTPException(status_code=401, detail="学院标识无效")
        bound = db.query(College.id).filter(College.id == college_id).scalar()
        if bound is None:
            raise HTTPException(status_code=403, detail="无权访问该范围的数据")
        scope = AccessScope.for_college(college_id)

    request.state.auth_scope = scope
    return scope


def require_read_scope(
    request: Request,
    db: Session = Depends(get_db),
    role_header: Optional[str] = Header(None, alias=ROLE_HEADER),
    college_header: Optional[str] = Header(None, alias=COLLEGE_HEADER),
) -> AccessScope:
    """受保护接口统一入口：解析并挂载调用方范围。"""
    return _build_scope(request, db, role_header, college_header)


def require_school_scope(scope: AccessScope) -> AccessScope:
    """写操作/全校能力仅限校级角色。"""
    if scope.role not in WRITE_ROLES:
        raise ScopeViolation("school_only", "role")
    return scope


def enforce_query_filters(
    scope: AccessScope,
    db: Session,
    *,
    college_id: Optional[int] = None,
    micro_major_id: Optional[int] = None,
) -> None:
    """组合筛选入口：每个显式传入的范围参数都必须落在授权范围内。

    未传参时不做拒绝——学院角色会在数据查询层被强制叠加学院过滤，
    因此“不带学院参数”对学院角色而言等于“本学院”，而不是全校。
    """
    if college_id is not None:
        require_college(scope, db, int(college_id))
    if micro_major_id is not None:
        require_micro_major(scope, db, int(micro_major_id))


def ensure_college_accessible(scope: AccessScope, db: Session, college_id: int):
    return require_college(scope, db, college_id)


def ensure_micro_major_accessible(scope: AccessScope, db: Session, micro_major_id: int):
    return require_micro_major(scope, db, micro_major_id)


def ensure_graduate_accessible(scope: AccessScope, db: Session, graduate_id: int):
    return require_graduate(scope, db, graduate_id)


def ensure_warning_accessible(scope: AccessScope, db: Session, warning_id: int):
    return require_warning(scope, db, warning_id)


def ensure_target_accessible(
    scope: AccessScope,
    db: Session,
    target_type: Optional[str],
    target_id: Optional[int],
) -> None:
    require_target(scope, db, target_type, target_id)


# ---------------------------------------------------------------------------
# 报告链接：每次访问都按调用方“当前”权限重新校验，不复用旧授权结论
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReportScopeKey:
    """一份报告链接的身份与绑定范围。"""

    report_type: str
    role: Role
    college_ids: frozenset[int]


def _sign(payload_b64: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def mint_report_link(
    report_type: str,
    scope: AccessScope,
    *,
    ttl_seconds: int = LINK_TTL_SECONDS,
) -> str:
    payload = {
        "v": 1,
        "report": report_type,
        "role": scope.role.value,
        "colleges": sorted(scope.college_ids),
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl_seconds,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = _sign(payload_b64, settings.REPORT_LINK_SECRET)
    return f"{payload_b64}.{signature}"


def verify_report_link(
    token: str,
    scope: AccessScope,
    report_type: str,
) -> dict:
    """校验链接签名、有效期，并以调用方当前范围重新比对绑定范围。

    任何失败都抛出 ScopeViolation（统一转 403，不暴露报告内容或缓存数字）。
    """
    try:
        payload_b64, signature = token.split(".", 1)
    except (AttributeError, ValueError):
        raise ScopeViolation("report_link_malformed", "report_link")

    expected = _sign(payload_b64, settings.REPORT_LINK_SECRET)
    if not hmac.compare_digest(expected, signature):
        raise ScopeViolation("report_link_bad_signature", "report_link")

    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except Exception:
        raise ScopeViolation("report_link_malformed", "report_link")

    if int(time.time()) > int(payload.get("exp", 0)):
        raise ScopeViolation("report_link_expired", "report_link")
    if payload.get("report") != report_type:
        raise ScopeViolation("report_link_report_mismatch", "report_link")

    bound_role = Role.parse(payload.get("role"))
    bound_colleges = frozenset(int(c) for c in payload.get("colleges", []))
    if bound_role is None or bound_role is not scope.role:
        raise ScopeViolation("report_link_role_changed", "report_link")

    # 关键：链接授予的学院集合必须仍被调用方当前范围完整覆盖。
    if scope.is_college and not bound_colleges <= scope.college_ids:
        raise ScopeViolation("report_link_scope_changed", "report_link")

    return payload


def audit_scope_violation(request: Request, exc: ScopeViolation) -> None:
    scope = getattr(request.state, "auth_scope", None)
    audit_log.record(
        role=scope.role.value if scope else "anonymous",
        method=request.method,
        path=request.url.path,
        reason=exc.reason,
        resource_type=exc.resource_type or "",
        resource_id=exc.resource_id,
        college_ids=tuple(scope.college_ids) if scope else (),
    )
