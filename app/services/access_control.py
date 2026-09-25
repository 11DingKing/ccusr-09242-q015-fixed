"""统一的调用方访问控制边界。

所有统计、预警、明细与报告接口都通过这里的 AccessContext 与校验函数
解析调用方可见的学院范围：

- 校级角色（allowed_college_ids 为 None）保留全量能力；
- 学院角色只能访问授权学院范围内的数据，跨范围参数必须被明确拒绝，
  而不是悄悄返回空数据；
- 校验函数只抛出不含敏感数值的通用拒绝原因，避免在响应与审计中泄露。
"""

from dataclasses import dataclass
from typing import Collection, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import Graduate, MicroMajor, UserAccount, UserRole, Warning

DENIED_MESSAGE = "请求的参数超出授权范围"


class AccessDeniedError(Exception):
    """调用方请求了授权范围之外的资源。"""

    def __init__(self, reason: str = DENIED_MESSAGE):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class AccessContext:
    """一次请求生效的授权范围。allowed_college_ids 为 None 表示校级全量。"""

    user_id: int
    username: str
    role: UserRole
    allowed_college_ids: Optional[frozenset]

    @property
    def is_school_wide(self) -> bool:
        return self.allowed_college_ids is None


def build_access_context(user: UserAccount, granted_college_ids: Collection[int]) -> AccessContext:
    """根据账号角色与授权记录构建访问上下文。"""

    if user.role == UserRole.SCHOOL_ADMIN:
        return AccessContext(user.id, user.username, user.role, None)
    return AccessContext(user.id, user.username, user.role, frozenset(granted_college_ids))


def resolve_college_scope(ctx: AccessContext, requested_college_id: Optional[int]) -> Optional[frozenset]:
    """把请求中的 college_id 参数解析为实际生效的学院过滤集合。

    返回 None 表示不做学院过滤（仅校级未指定参数时）。
    学院角色指定了范围外的学院时抛出 AccessDeniedError。
    """

    if ctx.is_school_wide:
        return frozenset([requested_college_id]) if requested_college_id is not None else None
    if requested_college_id is not None and requested_college_id not in ctx.allowed_college_ids:
        raise AccessDeniedError()
    return ctx.allowed_college_ids


def assert_college_allowed(ctx: AccessContext, college_id: int) -> None:
    """直接校验某个学院标识是否在授权范围内。"""

    if ctx.is_school_wide:
        return
    if college_id not in ctx.allowed_college_ids:
        raise AccessDeniedError()


def assert_micro_major_allowed(db: Session, ctx: AccessContext, micro_major_id: int) -> MicroMajor:
    """校验微专业归属的学院在授权范围内，返回微专业对象。"""

    micro_major = db.query(MicroMajor).filter(MicroMajor.id == micro_major_id).first()
    if not micro_major:
        raise LookupError("微专业不存在")
    assert_college_allowed(ctx, micro_major.college_id)
    return micro_major


def scoped_micro_major_ids(db: Session, ctx: AccessContext) -> Optional[list]:
    """学院角色可见的微专业标识集合；校级返回 None 表示不限制。"""

    if ctx.is_school_wide:
        return None
    rows = db.query(MicroMajor.id).filter(
        MicroMajor.college_id.in_(ctx.allowed_college_ids)
    ).all()
    return [row[0] for row in rows]


def graduate_scope_condition(ctx: AccessContext):
    """毕业生查询的学院范围条件；校级返回 None。"""

    if ctx.is_school_wide:
        return None
    return Graduate.college_id.in_(ctx.allowed_college_ids)


def warning_scope_condition(db: Session, ctx: AccessContext):
    """预警查询的范围条件：学院目标在授权内，或微专业目标归属授权学院。"""

    if ctx.is_school_wide:
        return None
    micro_ids = scoped_micro_major_ids(db, ctx) or []
    return or_(
        and_(
            Warning.target_type == "college",
            Warning.target_id.in_(ctx.allowed_college_ids),
        ),
        and_(
            Warning.target_type == "micro_major",
            Warning.target_id.in_(micro_ids),
        ),
    )


def assert_warning_allowed(db: Session, ctx: AccessContext, warning: Warning) -> None:
    """校验单条预警是否在调用方授权范围内。"""

    if ctx.is_school_wide:
        return
    if warning.target_type == "college":
        assert_college_allowed(ctx, warning.target_id)
        return
    if warning.target_type == "micro_major":
        assert_micro_major_allowed(db, ctx, warning.target_id)
        return
    raise AccessDeniedError()


def assert_graduate_allowed(ctx: AccessContext, graduate: Graduate) -> None:
    """校验单条毕业生明细是否在调用方授权范围内。"""

    assert_college_allowed(ctx, graduate.college_id)


def combine_scopes(*scopes: Optional[frozenset]) -> Optional[frozenset]:
    """求多个范围约束的交集；任一为 None 表示该方不限制。"""

    effective: Optional[frozenset] = None
    for scope in scopes:
        if scope is None:
            continue
        effective = scope if effective is None else effective & scope
    return effective
