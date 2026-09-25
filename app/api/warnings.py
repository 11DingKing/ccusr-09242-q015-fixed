from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.core import get_db
from app.core.security import (
    AccessScope,
    require_read_scope,
    require_school_scope,
    ensure_warning_accessible,
    ensure_target_accessible,
)
from app.models import (
    Warning,
    WarningStatus,
    AttributionRecord,
)
from app.schemas import (
    Warning as WarningSchema,
    WarningUpdate,
    WarningListItem,
    WarningListResponse,
)
from app.utils import run_full_warning_detection, run_warning_detection_for_target

router = APIRouter(prefix="/warnings", tags=["预警管理"])


@router.get("", response_model=WarningListResponse)
def list_warnings(
    status: Optional[str] = Query(None, description="预警状态：预警中/已解决/已忽略"),
    warning_type: Optional[str] = Query(None, description="预警类型"),
    warning_level: Optional[str] = Query(None, description="预警级别"),
    target_type: Optional[str] = Query(None, description="预警对象类型：micro_major/college"),
    target_id: Optional[int] = Query(None, description="预警对象ID"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    # 显式目标越权即 403，而非被范围过滤清空。
    ensure_target_accessible(scope, db, target_type, target_id)

    query = db.query(Warning)

    if status:
        query = query.filter(Warning.status == status)
    if warning_type:
        query = query.filter(Warning.warning_type == warning_type)
    if warning_level:
        query = query.filter(Warning.warning_level == warning_level)
    if target_type:
        query = query.filter(Warning.target_type == target_type)
    if target_id:
        query = query.filter(Warning.target_id == target_id)

    # 学院角色强制收口到本学院及其微专业预警。
    query = scope.apply_warning_filter(query, db)

    warnings = query.order_by(Warning.created_at.desc()).all()

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


@router.get("/{warning_id}", response_model=WarningSchema)
def get_warning(
    warning_id: int,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    return ensure_warning_accessible(scope, db, warning_id)


@router.put("/{warning_id}", response_model=WarningSchema)
def update_warning(
    warning_id: int,
    warning_in: WarningUpdate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    require_school_scope(scope)
    warning = db.query(Warning).filter(Warning.id == warning_id).first()
    if not warning:
        raise HTTPException(status_code=404, detail="预警不存在")

    update_data = warning_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(warning, key, value)

    db.commit()
    db.refresh(warning)
    return warning


@router.post("/detect")
def run_detection(
    target_type: Optional[str] = Query(None, description="对象类型：micro_major/college，不传则检测全部"),
    target_id: Optional[int] = Query(None, description="对象ID，与target_type配合使用"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    require_school_scope(scope)
    if target_type and target_id:
        warnings = run_warning_detection_for_target(db, target_type, target_id)
        return {
            "message": f"针对{target_type}#{target_id}的预警检测完成",
            "warnings_count": len(warnings),
            "warnings": [
                {
                    "id": w.id,
                    "type": w.warning_type.value,
                    "level": w.warning_level.value,
                    "description": w.description,
                }
                for w in warnings
            ],
        }
    else:
        result = run_full_warning_detection(db)
        return {
            "message": "全量预警检测完成",
            **result,
        }
