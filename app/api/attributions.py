from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from collections import Counter

from app.core import get_db
from app.core.security import (
    AccessScope,
    require_read_scope,
    require_school_scope,
    ensure_warning_accessible,
    ensure_target_accessible,
    accessible_warning_ids,
    ScopeViolation,
)
from app.models import (
    AttributionRecord,
    Warning,
    AttributionCategory,
)
from app.schemas import (
    AttributionRecord as AttributionRecordSchema,
    AttributionRecordCreate,
    AttributionRecordUpdate,
    AttributionDistributionResponse,
    AttributionDistributionItem,
)

router = APIRouter(prefix="/attributions", tags=["归因分析"])


def _get_record_in_scope(db: Session, scope: AccessScope, record_id: int) -> AttributionRecord:
    record = db.query(AttributionRecord).filter(AttributionRecord.id == record_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="归因记录不存在")
    warning = db.query(Warning).filter(Warning.id == record.warning_id).first()
    if warning is not None and not _warning_in_scope(scope, db, warning):
        raise ScopeViolation("attribution_out_of_scope", "attribution", record.id)
    return record


def _warning_in_scope(scope: AccessScope, db: Session, warning: Warning) -> bool:
    from app.core.security import warning_in_scope
    return warning_in_scope(scope, db, warning)


@router.get("", response_model=List[AttributionRecordSchema])
def list_attributions(
    warning_id: Optional[int] = Query(None, description="关联预警ID"),
    category: Optional[str] = Query(None, description="归因类别"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    # 显式 warning_id 越权即 403。
    if warning_id is not None:
        ensure_warning_accessible(scope, db, int(warning_id))

    # 数据层强制只取授权范围内预警的归因记录。
    allowed_warning_ids = accessible_warning_ids(scope, db)
    if not allowed_warning_ids:
        return []
    query = db.query(AttributionRecord).filter(
        AttributionRecord.warning_id.in_(allowed_warning_ids)
    )

    if warning_id:
        query = query.filter(AttributionRecord.warning_id == warning_id)
    if category:
        query = query.filter(AttributionRecord.category == category)

    return query.order_by(AttributionRecord.created_at.desc()).all()


@router.get("/distribution", response_model=AttributionDistributionResponse)
def get_attribution_distribution(
    target_type: Optional[str] = Query(None, description="对象类型：micro_major/college"),
    target_id: Optional[int] = Query(None, description="对象ID"),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    ensure_target_accessible(scope, db, target_type, target_id)

    allowed_warning_ids = accessible_warning_ids(scope, db)
    if not allowed_warning_ids:
        query = db.query(AttributionRecord).filter(False)
    else:
        query = db.query(AttributionRecord).filter(
            AttributionRecord.warning_id.in_(allowed_warning_ids)
        )

    if target_type or target_id:
        warning_query = db.query(Warning.id).filter(Warning.id.in_(allowed_warning_ids or [0]))
        if target_type:
            warning_query = warning_query.filter(Warning.target_type == target_type)
        if target_id:
            warning_query = warning_query.filter(Warning.target_id == target_id)
        warning_ids = [w[0] for w in warning_query.all()]
        if warning_ids:
            query = query.filter(AttributionRecord.warning_id.in_(warning_ids))
        else:
            query = query.filter(False)

    all_records = query.all()
    total_records = len(all_records)

    category_counter = Counter()
    examples_by_category = {}
    for record in all_records:
        cat_value = record.category.value if hasattr(record.category, 'value') else str(record.category)
        category_counter[cat_value] += 1
        if cat_value not in examples_by_category:
            examples_by_category[cat_value] = []
        if len(examples_by_category[cat_value]) < 3:
            warning = db.query(Warning).filter(Warning.id == record.warning_id).first()
            examples_by_category[cat_value].append({
                "record_id": record.id,
                "target_name": warning.target_name if warning else "未知",
                "target_type": warning.target_type if warning else "unknown",
                "description": record.description[:100] + "..." if len(record.description) > 100 else record.description,
            })

    distribution = []
    for cat in AttributionCategory:
        count = category_counter.get(cat.value, 0)
        percentage = round((count / total_records * 100), 2) if total_records > 0 else 0
        distribution.append(AttributionDistributionItem(
            category=cat.value,
            count=count,
            percentage=percentage,
            examples=examples_by_category.get(cat.value, []),
        ))

    target_counter = Counter()
    for record in all_records:
        warning = db.query(Warning).filter(Warning.id == record.warning_id).first()
        if warning:
            key = f"{warning.target_type}:{warning.target_id}:{warning.target_name}"
            target_counter[key] += 1

    top_targets = []
    for key, count in target_counter.most_common(5):
        this_target_type, this_target_id, target_name = key.split(":", 2)
        top_targets.append({
            "target_type": this_target_type,
            "target_id": int(this_target_id),
            "target_name": target_name,
            "attribution_count": count,
        })

    return AttributionDistributionResponse(
        total_records=total_records,
        distribution=distribution,
        top_targets=top_targets,
    )


@router.get("/{record_id}", response_model=AttributionRecordSchema)
def get_attribution(
    record_id: int,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    return _get_record_in_scope(db, scope, record_id)


@router.post("", response_model=AttributionRecordSchema)
def create_attribution(
    record_in: AttributionRecordCreate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    require_school_scope(scope)
    warning = db.query(Warning).filter(Warning.id == record_in.warning_id).first()
    if not warning:
        raise HTTPException(status_code=404, detail="关联预警不存在")

    record = AttributionRecord(**record_in.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.put("/{record_id}", response_model=AttributionRecordSchema)
def update_attribution(
    record_id: int,
    record_in: AttributionRecordUpdate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    require_school_scope(scope)
    record = db.query(AttributionRecord).filter(AttributionRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="归因记录不存在")

    update_data = record_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(record, key, value)

    db.commit()
    db.refresh(record)
    return record


@router.delete("/{record_id}")
def delete_attribution(
    record_id: int,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    require_school_scope(scope)
    record = db.query(AttributionRecord).filter(AttributionRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="归因记录不存在")

    db.delete(record)
    db.commit()
    return {"message": "删除成功"}
