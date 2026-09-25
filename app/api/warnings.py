from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime

from app.core import get_db, get_access_context, record_audit, deny_request
from app.models import (
    Warning,
    WarningStatus,
    WarningType,
    WarningLevel,
    AttributionRecord,
    MicroMajor,
)
from app.schemas import (
    Warning as WarningSchema,
    WarningUpdate,
    WarningListItem,
    WarningListResponse,
)
from app.services.access_control import (
    AccessContext,
    AccessDeniedError,
    assert_college_allowed,
    assert_micro_major_allowed,
    assert_warning_allowed,
    warning_scope_condition,
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
    ctx: AccessContext = Depends(get_access_context),
):
    action = "warnings.list"
    params = {
        "status": status,
        "warning_type": warning_type,
        "warning_level": warning_level,
        "target_type": target_type,
        "target_id": target_id,
    }

    if target_id is not None:
        try:
            if target_type == "micro_major":
                assert_micro_major_allowed(db, ctx, target_id)
            elif target_type == "college":
                assert_college_allowed(ctx, target_id)
            elif not ctx.is_school_wide:
                # 未指定类型：学院或微专业任一在授权范围内即可
                in_college_scope = target_id in ctx.allowed_college_ids
                mm = db.query(MicroMajor).filter(MicroMajor.id == target_id).first()
                in_micro_scope = mm is not None and mm.college_id in ctx.allowed_college_ids
                if not (in_college_scope or in_micro_scope):
                    raise AccessDeniedError()
        except AccessDeniedError as exc:
            deny_request(db, ctx, action, "/warnings", params, exc)
        except LookupError:
            raise HTTPException(status_code=404, detail="预警对象不存在")

    scope_condition = warning_scope_condition(db, ctx)

    query = db.query(Warning)
    if scope_condition is not None:
        query = query.filter(scope_condition)

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

    warnings = query.order_by(Warning.created_at.desc()).all()

    count_query = db.query(Warning)
    if scope_condition is not None:
        count_query = count_query.filter(scope_condition)
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

    record_audit(db, ctx, action, "/warnings", params)
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
    ctx: AccessContext = Depends(get_access_context),
):
    warning = db.query(Warning).filter(Warning.id == warning_id).first()
    if not warning:
        raise HTTPException(status_code=404, detail="预警不存在")
    try:
        assert_warning_allowed(db, ctx, warning)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "warnings.get", f"/warnings/{warning_id}", {"warning_id": warning_id}, exc)
    record_audit(db, ctx, "warnings.get", f"/warnings/{warning_id}", {"warning_id": warning_id})
    return warning


@router.put("/{warning_id}", response_model=WarningSchema)
def update_warning(
    warning_id: int,
    warning_in: WarningUpdate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    warning = db.query(Warning).filter(Warning.id == warning_id).first()
    if not warning:
        raise HTTPException(status_code=404, detail="预警不存在")
    try:
        assert_warning_allowed(db, ctx, warning)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "warnings.update", f"/warnings/{warning_id}", {"warning_id": warning_id}, exc)

    update_data = warning_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(warning, key, value)

    db.commit()
    db.refresh(warning)
    record_audit(db, ctx, "warnings.update", f"/warnings/{warning_id}", {"warning_id": warning_id})
    return warning


@router.post("/detect")
def run_detection(
    target_type: Optional[str] = Query(None, description="对象类型：micro_major/college，不传则检测全部"),
    target_id: Optional[int] = Query(None, description="对象ID，与target_type配合使用"),
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    action = "warnings.detect"
    params = {"target_type": target_type, "target_id": target_id}

    if target_type and target_id:
        try:
            if target_type == "micro_major":
                assert_micro_major_allowed(db, ctx, target_id)
            elif target_type == "college":
                assert_college_allowed(ctx, target_id)
            else:
                raise AccessDeniedError()
        except AccessDeniedError as exc:
            deny_request(db, ctx, action, "/warnings/detect", params, exc)
        except LookupError:
            raise HTTPException(status_code=404, detail="检测对象不存在")

        warnings = run_warning_detection_for_target(db, target_type, target_id)
        record_audit(db, ctx, action, "/warnings/detect", params)
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
        if not ctx.is_school_wide:
            deny_request(
                db, ctx, action, "/warnings/detect", params,
                AccessDeniedError("全量预警检测仅校级可用"),
            )
        result = run_full_warning_detection(db)
        record_audit(db, ctx, action, "/warnings/detect", params)
        return {
            "message": "全量预警检测完成",
            **result,
        }
