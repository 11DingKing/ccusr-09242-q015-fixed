from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from collections import Counter

from app.core import get_db, get_access_context, record_audit, deny_request
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
from app.services.access_control import (
    AccessContext,
    AccessDeniedError,
    assert_warning_allowed,
    warning_scope_condition,
)

router = APIRouter(prefix="/attributions", tags=["归因分析"])


def _load_record_or_404(db: Session, record_id: int) -> AttributionRecord:
    record = db.query(AttributionRecord).filter(AttributionRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="归因记录不存在")
    return record


def _assert_record_in_scope(db: Session, ctx: AccessContext, record: AttributionRecord, action: str, resource: str):
    warning = db.query(Warning).filter(Warning.id == record.warning_id).first()
    if warning is None:
        raise HTTPException(status_code=404, detail="关联预警不存在")
    try:
        assert_warning_allowed(db, ctx, warning)
    except AccessDeniedError as exc:
        deny_request(db, ctx, action, resource, {"record_id": record.id}, exc)


@router.get("", response_model=List[AttributionRecordSchema])
def list_attributions(
    warning_id: Optional[int] = Query(None, description="关联预警ID"),
    category: Optional[str] = Query(None, description="归因类别"),
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    action = "attributions.list"
    params = {"warning_id": warning_id, "category": category}

    if warning_id is not None:
        warning = db.query(Warning).filter(Warning.id == warning_id).first()
        if warning is None:
            raise HTTPException(status_code=404, detail="关联预警不存在")
        try:
            assert_warning_allowed(db, ctx, warning)
        except AccessDeniedError as exc:
            deny_request(db, ctx, action, "/attributions", params, exc)

    scope_condition = warning_scope_condition(db, ctx)

    query = db.query(AttributionRecord)
    if scope_condition is not None:
        scoped_warning_ids = db.query(Warning.id).filter(scope_condition)
        query = query.filter(AttributionRecord.warning_id.in_(scoped_warning_ids))

    if warning_id:
        query = query.filter(AttributionRecord.warning_id == warning_id)
    if category:
        query = query.filter(AttributionRecord.category == category)

    record_audit(db, ctx, action, "/attributions", params)
    return query.order_by(AttributionRecord.created_at.desc()).all()


@router.get("/distribution", response_model=AttributionDistributionResponse)
def get_attribution_distribution(
    target_type: Optional[str] = Query(None, description="对象类型：micro_major/college"),
    target_id: Optional[int] = Query(None, description="对象ID"),
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    action = "attributions.distribution"
    params = {"target_type": target_type, "target_id": target_id}

    scope_condition = warning_scope_condition(db, ctx)

    query = db.query(AttributionRecord)

    warning_query = db.query(Warning.id)
    if scope_condition is not None:
        warning_query = warning_query.filter(scope_condition)
    if target_type:
        warning_query = warning_query.filter(Warning.target_type == target_type)
    if target_id:
        warning_query = warning_query.filter(Warning.target_id == target_id)
    if scope_condition is not None or target_type or target_id:
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
        t_type, t_id, t_name = key.split(":", 2)
        top_targets.append({
            "target_type": t_type,
            "target_id": int(t_id),
            "target_name": t_name,
            "attribution_count": count,
        })

    record_audit(db, ctx, action, "/attributions/distribution", params)
    return AttributionDistributionResponse(
        total_records=total_records,
        distribution=distribution,
        top_targets=top_targets,
    )


@router.get("/{record_id}", response_model=AttributionRecordSchema)
def get_attribution(
    record_id: int,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    record = _load_record_or_404(db, record_id)
    _assert_record_in_scope(db, ctx, record, "attributions.get", f"/attributions/{record_id}")
    record_audit(db, ctx, "attributions.get", f"/attributions/{record_id}", {"record_id": record_id})
    return record


@router.post("", response_model=AttributionRecordSchema)
def create_attribution(
    record_in: AttributionRecordCreate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    warning = db.query(Warning).filter(Warning.id == record_in.warning_id).first()
    if not warning:
        raise HTTPException(status_code=404, detail="关联预警不存在")
    try:
        assert_warning_allowed(db, ctx, warning)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "attributions.create", "/attributions", {"warning_id": record_in.warning_id}, exc)

    record = AttributionRecord(**record_in.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    record_audit(db, ctx, "attributions.create", "/attributions", {"warning_id": record_in.warning_id})
    return record


@router.put("/{record_id}", response_model=AttributionRecordSchema)
def update_attribution(
    record_id: int,
    record_in: AttributionRecordUpdate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    record = _load_record_or_404(db, record_id)
    _assert_record_in_scope(db, ctx, record, "attributions.update", f"/attributions/{record_id}")

    update_data = record_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(record, key, value)

    db.commit()
    db.refresh(record)
    record_audit(db, ctx, "attributions.update", f"/attributions/{record_id}", {"record_id": record_id})
    return record


@router.delete("/{record_id}")
def delete_attribution(
    record_id: int,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    record = _load_record_or_404(db, record_id)
    _assert_record_in_scope(db, ctx, record, "attributions.delete", f"/attributions/{record_id}")

    db.delete(record)
    db.commit()
    record_audit(db, ctx, "attributions.delete", f"/attributions/{record_id}", {"record_id": record_id})
    return {"message": "删除成功"}
