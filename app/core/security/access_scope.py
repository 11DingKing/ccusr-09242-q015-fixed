"""授权范围（AccessScope）及其强制校验。

校级角色 ``college_ids`` 为空集合表示全量；学院角色必须精确持有一个或多个
学院标识，所有学院、微专业、毕业生、预警、归因资源都按该集合判定归属。
"""

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import College, MicroMajor, Graduate, Warning
from .roles import Role


COLLEGE_TARGET = "college"
MICRO_MAJOR_TARGET = "micro_major"
VALID_TARGET_TYPES = frozenset({COLLEGE_TARGET, MICRO_MAJOR_TARGET})


class AccessDenied(HTTPException):
    """越权访问统一抛出 403，响应只含固定文案，不回显任何统计数字。"""

    def __init__(self, reason: str = "out_of_scope") -> None:
        super().__init__(status_code=403, detail="无权访问该范围的数据")
        self.reason = reason


class ScopeViolation(Exception):
    """内部使用的越权信号，携带可审计的安全原因码（不含敏感数据）。"""

    def __init__(self, reason: str, resource_type: str = "", resource_id: Optional[int] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.resource_type = resource_type
        self.resource_id = resource_id


@dataclass(frozen=True)
class AccessScope:
    """一次请求解析出的调用方授权范围。"""

    role: Role
    college_ids: frozenset[int] = frozenset()

    @property
    def is_school(self) -> bool:
        return self.role is Role.SCHOOL

    @property
    def is_college(self) -> bool:
        return self.role is Role.COLLEGE

    @classmethod
    def school(cls) -> "AccessScope":
        return cls(role=Role.SCHOOL)

    @classmethod
    def for_college(cls, college_id: int) -> "AccessScope":
        return cls(role=Role.COLLEGE, college_ids=frozenset({int(college_id)}))

    def covers_college(self, college_id: int) -> bool:
        if self.is_school:
            return True
        return int(college_id) in self.college_ids

    def covers_micro_major(self, db: Session, micro_major_id: int) -> bool:
        if self.is_school:
            return True
        owner_id = db.query(MicroMajor.college_id).filter(
            MicroMajor.id == micro_major_id
        ).scalar()
        return owner_id is not None and int(owner_id) in self.college_ids

    def own_micro_major_ids(self, db: Session) -> list[int]:
        if self.is_school:
            return [row[0] for row in db.query(MicroMajor.id).all()]
        if not self.college_ids:
            return []
        return [
            row[0]
            for row in db.query(MicroMajor.id).filter(
                MicroMajor.college_id.in_(tuple(self.college_ids))
            ).all()
        ]

    def graduate_base_query(self, db: Session):
        """毕业生查询的强制范围过滤（校级不加过滤）。"""
        query = db.query(Graduate)
        if self.is_college:
            query = query.filter(Graduate.college_id.in_(tuple(self.college_ids)))
        return query

    def apply_warning_filter(self, query, db: Session):
        """在预警查询上强制叠加学院/微专业归属条件。"""
        if self.is_school:
            return query
        from sqlalchemy import or_, and_

        mm_ids = self.own_micro_major_ids(db)
        college_clause = and_(
            Warning.target_type == COLLEGE_TARGET,
            Warning.target_id.in_(tuple(self.college_ids)),
        )
        if mm_ids:
            return query.filter(or_(
                college_clause,
                and_(
                    Warning.target_type == MICRO_MAJOR_TARGET,
                    Warning.target_id.in_(tuple(mm_ids)),
                ),
            ))
        return query.filter(college_clause)


def ensure_college_exists(db: Session, college_id: int) -> College:
    college = db.query(College).filter(College.id == college_id).first()
    if college is None:
        raise HTTPException(status_code=404, detail="学院不存在")
    return college


def ensure_micro_major_exists(db: Session, micro_major_id: int) -> MicroMajor:
    micro_major = db.query(MicroMajor).filter(MicroMajor.id == micro_major_id).first()
    if micro_major is None:
        raise HTTPException(status_code=404, detail="微专业不存在")
    return micro_major


def require_college(scope: AccessScope, db: Session, college_id: int) -> College:
    """学院参数必须存在且在调用方范围内，否则 404/403，绝不静默返回空数据。"""
    college = ensure_college_exists(db, college_id)
    if not scope.covers_college(college.id):
        raise ScopeViolation("college_out_of_scope", COLLEGE_TARGET, college.id)
    return college


def require_micro_major(scope: AccessScope, db: Session, micro_major_id: int) -> MicroMajor:
    micro_major = ensure_micro_major_exists(db, micro_major_id)
    if not scope.covers_micro_major(db, micro_major.id):
        raise ScopeViolation("micro_major_out_of_scope", MICRO_MAJOR_TARGET, micro_major.id)
    return micro_major


def require_graduate(scope: AccessScope, db: Session, graduate_id: int) -> Graduate:
    graduate = db.query(Graduate).filter(Graduate.id == graduate_id).first()
    if graduate is None:
        raise HTTPException(status_code=404, detail="毕业生不存在")
    if not scope.covers_college(graduate.college_id):
        raise ScopeViolation("graduate_out_of_scope", "graduate", graduate.id)
    return graduate


def require_target(
    scope: AccessScope,
    db: Session,
    target_type: Optional[str],
    target_id: Optional[int],
) -> None:
    """校验预警/归因对象参数（college/micro_major）的归属。

    仅传 target_type 时只做合法性检查（它只是一个类型过滤）；
    只要带了 target_id，就必须能定位到范围内对象，否则 404/403。
    """
    if target_type is None:
        if target_id is None:
            return
        # 未指定类型时：该ID若命中任何越权预警目标，必须显式拒绝。
        if not scope.is_school:
            existing = db.query(Warning).filter(Warning.target_id == int(target_id)).all()
            if existing and not all(warning_in_scope(scope, db, w) for w in existing):
                raise ScopeViolation("target_out_of_scope", "warning_target", int(target_id))
        return
    if target_type not in VALID_TARGET_TYPES:
        raise HTTPException(status_code=400, detail="对象类型必须为 college 或 micro_major")
    if target_id is None:
        return
    if target_type == COLLEGE_TARGET:
        require_college(scope, db, int(target_id))
    else:
        require_micro_major(scope, db, int(target_id))


def warning_in_scope(scope: AccessScope, db: Session, warning: Warning) -> bool:
    if scope.is_school:
        return True
    if warning.target_type == COLLEGE_TARGET:
        return warning.target_id in scope.college_ids
    if warning.target_type == MICRO_MAJOR_TARGET:
        owner_id = db.query(MicroMajor.college_id).filter(
            MicroMajor.id == warning.target_id
        ).scalar()
        return owner_id is not None and owner_id in scope.college_ids
    return False


def require_warning(scope: AccessScope, db: Session, warning_id: int) -> Warning:
    warning = db.query(Warning).filter(Warning.id == warning_id).first()
    if warning is None:
        raise HTTPException(status_code=404, detail="预警不存在")
    if not warning_in_scope(scope, db, warning):
        raise ScopeViolation("warning_out_of_scope", "warning", warning.id)
    return warning


def accessible_warning_ids(scope: AccessScope, db: Session) -> list[int]:
    query = scope.apply_warning_filter(db.query(Warning.id), db)
    return [row[0] for row in query.all()]
