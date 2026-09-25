from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import get_db
from app.core.security import (
    AccessScope,
    require_read_scope,
    require_school_scope,
    ensure_college_accessible,
)
from app.models import College
from app.schemas import (
    College as CollegeSchema,
    CollegeCreate,
    CollegeUpdate,
    CollegeProfile,
)
from app.utils import build_college_profile, run_warning_detection_for_target

router = APIRouter(prefix="/colleges", tags=["学院管理"])


@router.get("", response_model=List[CollegeSchema])
def list_colleges(
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    # 学院角色只能看到本学院基础信息，不获得其他学院编号清单。
    query = db.query(College)
    if scope.is_college:
        query = query.filter(College.id.in_(tuple(scope.college_ids)))
    return query.order_by(College.id).all()


@router.post("", response_model=CollegeSchema)
def create_college(
    college_in: CollegeCreate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    require_school_scope(scope)
    existing = db.query(College).filter(
        (College.name == college_in.name) | (College.code == college_in.code)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="学院名称或代码已存在")

    college = College(**college_in.model_dump())
    db.add(college)
    db.commit()
    db.refresh(college)
    return college


@router.get("/{college_id}", response_model=CollegeSchema)
def get_college(
    college_id: int,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    return ensure_college_accessible(scope, db, college_id)


@router.get("/{college_id}/profile", response_model=CollegeProfile)
def get_college_profile(
    college_id: int,
    run_detection: bool = False,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    ensure_college_accessible(scope, db, college_id)

    if run_detection:
        require_school_scope(scope)
        run_warning_detection_for_target(db, "college", college_id)

    profile = build_college_profile(db, college_id, scope=scope)
    if not profile:
        raise HTTPException(status_code=404, detail="无法生成成效画像")
    return profile


@router.put("/{college_id}", response_model=CollegeSchema)
def update_college(
    college_id: int,
    college_in: CollegeUpdate,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    require_school_scope(scope)
    college = db.query(College).filter(College.id == college_id).first()
    if not college:
        raise HTTPException(status_code=404, detail="学院不存在")

    update_data = college_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(college, key, value)

    db.commit()
    db.refresh(college)
    return college


@router.delete("/{college_id}")
def delete_college(
    college_id: int,
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(require_read_scope),
):
    require_school_scope(scope)
    college = db.query(College).filter(College.id == college_id).first()
    if not college:
        raise HTTPException(status_code=404, detail="学院不存在")

    db.delete(college)
    db.commit()
    return {"message": "删除成功"}
