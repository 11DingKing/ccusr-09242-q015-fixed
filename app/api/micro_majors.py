from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import get_db, get_access_context, record_audit, deny_request, require_school_wide
from app.models import MicroMajor
from app.schemas import (
    MicroMajor as MicroMajorSchema,
    MicroMajorCreate,
    MicroMajorUpdate,
    MicroMajorProfile,
)
from app.services.access_control import (
    AccessContext,
    AccessDeniedError,
    assert_micro_major_allowed,
)
from app.utils import build_micro_major_profile, run_warning_detection_for_target

router = APIRouter(prefix="/micro-majors", tags=["微专业管理"])


@router.get("", response_model=List[MicroMajorSchema])
def list_micro_majors(
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    return db.query(MicroMajor).order_by(MicroMajor.id).all()


@router.post("", response_model=MicroMajorSchema)
def create_micro_major(
    micro_major_in: MicroMajorCreate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    try:
        require_school_wide(ctx)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "micro_majors.create", "/micro-majors", None, exc)

    existing = db.query(MicroMajor).filter(
        (MicroMajor.name == micro_major_in.name) | (MicroMajor.code == micro_major_in.code)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="微专业名称或代码已存在")

    micro_major = MicroMajor(**micro_major_in.model_dump())
    db.add(micro_major)
    db.commit()
    db.refresh(micro_major)
    record_audit(db, ctx, "micro_majors.create", "/micro-majors")
    return micro_major


@router.get("/{micro_major_id}", response_model=MicroMajorSchema)
def get_micro_major(
    micro_major_id: int,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    micro_major = db.query(MicroMajor).filter(MicroMajor.id == micro_major_id).first()
    if not micro_major:
        raise HTTPException(status_code=404, detail="微专业不存在")
    return micro_major


@router.get("/{micro_major_id}/profile", response_model=MicroMajorProfile)
def get_micro_major_profile(
    micro_major_id: int,
    run_detection: bool = False,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    try:
        assert_micro_major_allowed(db, ctx, micro_major_id)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "micro_majors.profile", f"/micro-majors/{micro_major_id}/profile", {"micro_major_id": micro_major_id}, exc)
    except LookupError:
        raise HTTPException(status_code=404, detail="微专业不存在")

    if run_detection:
        run_warning_detection_for_target(db, "micro_major", micro_major_id)

    profile = build_micro_major_profile(db, micro_major_id)
    if not profile:
        raise HTTPException(status_code=404, detail="无法生成成效画像")
    record_audit(db, ctx, "micro_majors.profile", f"/micro-majors/{micro_major_id}/profile", {"micro_major_id": micro_major_id})
    return profile


@router.put("/{micro_major_id}", response_model=MicroMajorSchema)
def update_micro_major(
    micro_major_id: int,
    micro_major_in: MicroMajorUpdate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    try:
        require_school_wide(ctx)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "micro_majors.update", f"/micro-majors/{micro_major_id}", {"micro_major_id": micro_major_id}, exc)

    micro_major = db.query(MicroMajor).filter(MicroMajor.id == micro_major_id).first()
    if not micro_major:
        raise HTTPException(status_code=404, detail="微专业不存在")

    update_data = micro_major_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(micro_major, key, value)

    db.commit()
    db.refresh(micro_major)
    record_audit(db, ctx, "micro_majors.update", f"/micro-majors/{micro_major_id}", {"micro_major_id": micro_major_id})
    return micro_major


@router.delete("/{micro_major_id}")
def delete_micro_major(
    micro_major_id: int,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    try:
        require_school_wide(ctx)
    except AccessDeniedError as exc:
        deny_request(db, ctx, "micro_majors.delete", f"/micro-majors/{micro_major_id}", {"micro_major_id": micro_major_id}, exc)

    micro_major = db.query(MicroMajor).filter(MicroMajor.id == micro_major_id).first()
    if not micro_major:
        raise HTTPException(status_code=404, detail="微专业不存在")

    db.delete(micro_major)
    db.commit()
    record_audit(db, ctx, "micro_majors.delete", f"/micro-majors/{micro_major_id}", {"micro_major_id": micro_major_id})
    return {"message": "删除成功"}
