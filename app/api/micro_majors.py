from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import get_db
from app.core.security import (
    AccessScope,
    require_read_scope,
    require_school_scope,
    ensure_micro_major_accessible,
)
from app.models import MicroMajor
from app.schemas import (
    MicroMajor as MicroMajorSchema,
    MicroMajorCreate,
    MicroMajorUpdate,
    MicroMajorProfile,
)
from app.utils import build_micro_major_profile, run_warning_detection_for_target

router = APIRouter(prefix="/micro-majors", tags=["微专业管理"])


@router.get("", response_model=List[MicroMajorSchema])
def list_micro_majors(
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    # 学院角色只能枚举本学院微专业。
    query = db.query(MicroMajor)
    if scope.is_college:
        query = query.filter(MicroMajor.college_id.in_(tuple(scope.college_ids)))
    return query.order_by(MicroMajor.id).all()


@router.post("", response_model=MicroMajorSchema)
def create_micro_major(
    micro_major_in: MicroMajorCreate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    require_school_scope(scope)
    existing = db.query(MicroMajor).filter(
        (MicroMajor.name == micro_major_in.name) | (MicroMajor.code == micro_major_in.code)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="微专业名称或代码已存在")

    micro_major = MicroMajor(**micro_major_in.model_dump())
    db.add(micro_major)
    db.commit()
    db.refresh(micro_major)
    return micro_major


@router.get("/{micro_major_id}", response_model=MicroMajorSchema)
def get_micro_major(
    micro_major_id: int,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    return ensure_micro_major_accessible(scope, db, micro_major_id)


@router.get("/{micro_major_id}/profile", response_model=MicroMajorProfile)
def get_micro_major_profile(
    micro_major_id: int,
    run_detection: bool = False,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    ensure_micro_major_accessible(scope, db, micro_major_id)

    if run_detection:
        require_school_scope(scope)
        run_warning_detection_for_target(db, "micro_major", micro_major_id)

    profile = build_micro_major_profile(db, micro_major_id, scope=scope)
    if not profile:
        raise HTTPException(status_code=404, detail="无法生成成效画像")
    return profile


@router.put("/{micro_major_id}", response_model=MicroMajorSchema)
def update_micro_major(
    micro_major_id: int,
    micro_major_in: MicroMajorUpdate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    require_school_scope(scope)
    micro_major = db.query(MicroMajor).filter(MicroMajor.id == micro_major_id).first()
    if not micro_major:
        raise HTTPException(status_code=404, detail="微专业不存在")

    update_data = micro_major_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(micro_major, key, value)

    db.commit()
    db.refresh(micro_major)
    return micro_major


@router.delete("/{micro_major_id}")
def delete_micro_major(
    micro_major_id: int,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    require_school_scope(scope)
    micro_major = db.query(MicroMajor).filter(MicroMajor.id == micro_major_id).first()
    if not micro_major:
        raise HTTPException(status_code=404, detail="微专业不存在")

    db.delete(micro_major)
    db.commit()
    return {"message": "删除成功"}
