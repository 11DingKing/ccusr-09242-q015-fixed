from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.core import get_db, get_access_context, record_audit, deny_request, require_school_wide
from app.models import ProvinceReferenceLine
from app.schemas import (
    ProvinceReferenceLine as ProvinceReferenceLineSchema,
    ProvinceReferenceLineCreate,
    ProvinceReferenceLineUpdate,
)
from app.services.access_control import AccessContext, AccessDeniedError

router = APIRouter(prefix="/reference_lines", tags=["省基准线管理"])


@router.get("", response_model=List[ProvinceReferenceLineSchema])
def list_reference_lines(
    graduation_year: Optional[int] = Query(None, description="毕业届次"),
    indicator: Optional[str] = Query(None, description="指标名称"),
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    query = db.query(ProvinceReferenceLine)

    if graduation_year:
        query = query.filter(ProvinceReferenceLine.graduation_year == graduation_year)
    if indicator:
        query = query.filter(ProvinceReferenceLine.indicator == indicator)

    return query.order_by(
        ProvinceReferenceLine.graduation_year.desc(),
        ProvinceReferenceLine.indicator,
    ).all()


@router.get("/{reference_line_id}", response_model=ProvinceReferenceLineSchema)
def get_reference_line(
    reference_line_id: int,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    reference_line = db.query(ProvinceReferenceLine).filter(ProvinceReferenceLine.id == reference_line_id).first()
    if not reference_line:
        raise HTTPException(status_code=404, detail="基准线不存在")
    return reference_line


@router.post("", response_model=ProvinceReferenceLineSchema)
def create_reference_line(
    reference_line_in: ProvinceReferenceLineCreate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    try:
        require_school_wide(ctx)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "reference_lines.create", "/reference_lines", None, exc)

    existing = db.query(ProvinceReferenceLine).filter(
        ProvinceReferenceLine.graduation_year == reference_line_in.graduation_year,
        ProvinceReferenceLine.indicator == reference_line_in.indicator,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="该届次该指标的基准线已存在")

    reference_line = ProvinceReferenceLine(**reference_line_in.model_dump())
    db.add(reference_line)
    db.commit()
    db.refresh(reference_line)
    record_audit(db, ctx, "reference_lines.create", "/reference_lines")
    return reference_line


@router.put("/{reference_line_id}", response_model=ProvinceReferenceLineSchema)
def update_reference_line(
    reference_line_id: int,
    reference_line_in: ProvinceReferenceLineUpdate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    try:
        require_school_wide(ctx)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "reference_lines.update", f"/reference_lines/{reference_line_id}", None, exc)

    reference_line = db.query(ProvinceReferenceLine).filter(ProvinceReferenceLine.id == reference_line_id).first()
    if not reference_line:
        raise HTTPException(status_code=404, detail="基准线不存在")

    update_data = reference_line_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(reference_line, key, value)

    db.commit()
    db.refresh(reference_line)
    record_audit(db, ctx, "reference_lines.update", f"/reference_lines/{reference_line_id}")
    return reference_line


@router.delete("/{reference_line_id}")
def delete_reference_line(
    reference_line_id: int,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    try:
        require_school_wide(ctx)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "reference_lines.delete", f"/reference_lines/{reference_line_id}", None, exc)

    reference_line = db.query(ProvinceReferenceLine).filter(ProvinceReferenceLine.id == reference_line_id).first()
    if not reference_line:
        raise HTTPException(status_code=404, detail="基准线不存在")

    db.delete(reference_line)
    db.commit()
    record_audit(db, ctx, "reference_lines.delete", f"/reference_lines/{reference_line_id}")
    return {"message": "删除成功"}
