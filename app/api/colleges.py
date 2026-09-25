from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import get_db, get_access_context, record_audit, deny_request, require_school_wide
from app.models import College
from app.schemas import (
    College as CollegeSchema,
    CollegeCreate,
    CollegeUpdate,
    CollegeProfile,
)
from app.services.access_control import (
    AccessContext,
    AccessDeniedError,
    assert_college_allowed,
)
from app.utils import build_college_profile, run_warning_detection_for_target

router = APIRouter(prefix="/colleges", tags=["学院管理"])


@router.get("", response_model=List[CollegeSchema])
def list_colleges(
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    return db.query(College).order_by(College.id).all()


@router.post("", response_model=CollegeSchema)
def create_college(
    college_in: CollegeCreate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    try:
        require_school_wide(ctx)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "colleges.create", "/colleges", None, exc)

    existing = db.query(College).filter(
        (College.name == college_in.name) | (College.code == college_in.code)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="学院名称或代码已存在")

    college = College(**college_in.model_dump())
    db.add(college)
    db.commit()
    db.refresh(college)
    record_audit(db, ctx, "colleges.create", "/colleges")
    return college


@router.get("/{college_id}", response_model=CollegeSchema)
def get_college(
    college_id: int,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    college = db.query(College).filter(College.id == college_id).first()
    if not college:
        raise HTTPException(status_code=404, detail="学院不存在")
    return college


@router.get("/{college_id}/profile", response_model=CollegeProfile)
def get_college_profile(
    college_id: int,
    run_detection: bool = False,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    college = db.query(College).filter(College.id == college_id).first()
    if not college:
        raise HTTPException(status_code=404, detail="学院不存在")
    try:
        assert_college_allowed(ctx, college_id)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "colleges.profile", f"/colleges/{college_id}/profile", {"college_id": college_id}, exc)

    if run_detection:
        run_warning_detection_for_target(db, "college", college_id)

    profile = build_college_profile(db, college_id)
    if not profile:
        raise HTTPException(status_code=404, detail="无法生成成效画像")
    record_audit(db, ctx, "colleges.profile", f"/colleges/{college_id}/profile", {"college_id": college_id})
    return profile


@router.put("/{college_id}", response_model=CollegeSchema)
def update_college(
    college_id: int,
    college_in: CollegeUpdate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    try:
        require_school_wide(ctx)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "colleges.update", f"/colleges/{college_id}", {"college_id": college_id}, exc)

    college = db.query(College).filter(College.id == college_id).first()
    if not college:
        raise HTTPException(status_code=404, detail="学院不存在")

    update_data = college_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(college, key, value)

    db.commit()
    db.refresh(college)
    record_audit(db, ctx, "colleges.update", f"/colleges/{college_id}", {"college_id": college_id})
    return college


@router.delete("/{college_id}")
def delete_college(
    college_id: int,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    try:
        require_school_wide(ctx)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "colleges.delete", f"/colleges/{college_id}", {"college_id": college_id}, exc)

    college = db.query(College).filter(College.id == college_id).first()
    if not college:
        raise HTTPException(status_code=404, detail="学院不存在")

    db.delete(college)
    db.commit()
    record_audit(db, ctx, "colleges.delete", f"/colleges/{college_id}", {"college_id": college_id})
    return {"message": "删除成功"}
