from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.core import get_db, get_access_context, record_audit, deny_request
from app.models import Graduate, StatusChangeLog, DestinationStatus, DestinationType
from app.schemas import (
    Graduate as GraduateSchema,
    GraduateCreate,
    GraduateUpdate,
    StatusUpdateRequest,
    StatusChangeLog as StatusLogSchema,
)
from app.services.access_control import (
    AccessContext,
    AccessDeniedError,
    assert_college_allowed,
    assert_graduate_allowed,
    assert_micro_major_allowed,
    resolve_college_scope,
)

router = APIRouter(prefix="/graduates", tags=["毕业生管理"])


def _load_graduate_or_404(db: Session, graduate_id: int) -> Graduate:
    graduate = db.query(Graduate).filter(Graduate.id == graduate_id).first()
    if not graduate:
        raise HTTPException(status_code=404, detail="毕业生不存在")
    return graduate


def _assert_graduate_in_scope(db: Session, ctx: AccessContext, graduate: Graduate, action: str, resource: str):
    try:
        assert_graduate_allowed(ctx, graduate)
    except AccessDeniedError as exc:
        deny_request(db, ctx, action, resource, {"graduate_id": graduate.id}, exc)


@router.get("", response_model=List[GraduateSchema])
def list_graduates(
    graduation_year: Optional[int] = Query(None, description="毕业届次"),
    college_id: Optional[int] = Query(None, description="学院ID"),
    micro_major_id: Optional[int] = Query(None, description="微专业ID"),
    has_micro_major: Optional[bool] = Query(None, description="是否修读微专业"),
    destination_status: Optional[str] = Query(None, description="去向状态"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    action = "graduates.list"
    params = {
        "graduation_year": graduation_year,
        "college_id": college_id,
        "micro_major_id": micro_major_id,
        "has_micro_major": has_micro_major,
        "destination_status": destination_status,
    }
    try:
        college_scope = resolve_college_scope(ctx, college_id)
        if micro_major_id is not None:
            assert_micro_major_allowed(db, ctx, micro_major_id)
    except AccessDeniedError as exc:
        deny_request(db, ctx, action, "/graduates", params, exc)
    except LookupError:
        raise HTTPException(status_code=404, detail="微专业不存在")

    query = db.query(Graduate)

    filters = []
    if college_scope is not None:
        filters.append(Graduate.college_id.in_(college_scope))
    if graduation_year:
        filters.append(Graduate.graduation_year == graduation_year)
    if micro_major_id:
        filters.append(Graduate.micro_major_id == micro_major_id)
    if has_micro_major is not None:
        filters.append(Graduate.has_micro_major == has_micro_major)
    if destination_status:
        status_enum = None
        for ds in DestinationStatus:
            if ds.value == destination_status:
                status_enum = ds
                break
        if status_enum is None:
            status_enum = destination_status
        filters.append(Graduate.destination_status == status_enum)

    if filters:
        query = query.filter(and_(*filters))

    record_audit(db, ctx, action, "/graduates", params)
    return query.order_by(Graduate.graduation_year.desc(), Graduate.id).offset(skip).limit(limit).all()


@router.post("", response_model=GraduateSchema)
def create_graduate(
    graduate_in: GraduateCreate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    action = "graduates.create"
    params = {"college_id": graduate_in.college_id, "micro_major_id": graduate_in.micro_major_id}
    try:
        assert_college_allowed(ctx, graduate_in.college_id)
        if graduate_in.micro_major_id is not None:
            assert_micro_major_allowed(db, ctx, graduate_in.micro_major_id)
    except AccessDeniedError as exc:
        deny_request(db, ctx, action, "/graduates", params, exc)
    except LookupError:
        raise HTTPException(status_code=404, detail="微专业不存在")

    existing = db.query(Graduate).filter(Graduate.student_id == graduate_in.student_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="该学号已存在")

    graduate = Graduate(**graduate_in.model_dump())
    db.add(graduate)
    db.flush()

    log = StatusChangeLog(
        graduate_id=graduate.id,
        old_status=None,
        new_status=graduate.destination_status,
        changed_by=ctx.username,
        remark="初始建档"
    )
    db.add(log)
    db.commit()
    db.refresh(graduate)
    record_audit(db, ctx, action, "/graduates", params)
    return graduate


@router.get("/{graduate_id}", response_model=GraduateSchema)
def get_graduate(
    graduate_id: int,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    graduate = _load_graduate_or_404(db, graduate_id)
    _assert_graduate_in_scope(db, ctx, graduate, "graduates.get", f"/graduates/{graduate_id}")
    record_audit(db, ctx, "graduates.get", f"/graduates/{graduate_id}", {"graduate_id": graduate_id})
    return graduate


@router.put("/{graduate_id}", response_model=GraduateSchema)
def update_graduate(
    graduate_id: int,
    graduate_in: GraduateUpdate,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    graduate = _load_graduate_or_404(db, graduate_id)
    _assert_graduate_in_scope(db, ctx, graduate, "graduates.update", f"/graduates/{graduate_id}")

    update_data = graduate_in.model_dump(exclude_unset=True)
    try:
        if "college_id" in update_data and update_data["college_id"] is not None:
            assert_college_allowed(ctx, update_data["college_id"])
        if "micro_major_id" in update_data and update_data["micro_major_id"] is not None:
            assert_micro_major_allowed(db, ctx, update_data["micro_major_id"])
    except AccessDeniedError as exc:
        deny_request(db, ctx, "graduates.update", f"/graduates/{graduate_id}", {"graduate_id": graduate_id}, exc)
    except LookupError:
        raise HTTPException(status_code=404, detail="微专业不存在")

    for key, value in update_data.items():
        setattr(graduate, key, value)

    db.commit()
    db.refresh(graduate)
    record_audit(db, ctx, "graduates.update", f"/graduates/{graduate_id}", {"graduate_id": graduate_id})
    return graduate


@router.delete("/{graduate_id}")
def delete_graduate(
    graduate_id: int,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    graduate = _load_graduate_or_404(db, graduate_id)
    _assert_graduate_in_scope(db, ctx, graduate, "graduates.delete", f"/graduates/{graduate_id}")

    db.query(StatusChangeLog).filter(StatusChangeLog.graduate_id == graduate_id).delete()
    db.delete(graduate)
    db.commit()
    record_audit(db, ctx, "graduates.delete", f"/graduates/{graduate_id}", {"graduate_id": graduate_id})
    return {"message": "删除成功"}


@router.post("/{graduate_id}/status", response_model=GraduateSchema)
def update_status(
    graduate_id: int,
    status_in: StatusUpdateRequest,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    graduate = _load_graduate_or_404(db, graduate_id)
    _assert_graduate_in_scope(db, ctx, graduate, "graduates.update_status", f"/graduates/{graduate_id}/status")

    old_status = graduate.destination_status

    if old_status == status_in.new_status:
        return graduate

    log = StatusChangeLog(
        graduate_id=graduate.id,
        old_status=old_status,
        new_status=status_in.new_status,
        changed_by=status_in.changed_by,
        remark=status_in.remark
    )
    db.add(log)

    graduate.destination_status = status_in.new_status
    db.commit()
    db.refresh(graduate)
    record_audit(db, ctx, "graduates.update_status", f"/graduates/{graduate_id}/status", {"graduate_id": graduate_id})
    return graduate


@router.get("/{graduate_id}/status-logs", response_model=List[StatusLogSchema])
def get_status_logs(
    graduate_id: int,
    db: Session = Depends(get_db),
    ctx: AccessContext = Depends(get_access_context),
):
    graduate = _load_graduate_or_404(db, graduate_id)
    _assert_graduate_in_scope(db, ctx, graduate, "graduates.status_logs", f"/graduates/{graduate_id}/status-logs")

    record_audit(db, ctx, "graduates.status_logs", f"/graduates/{graduate_id}/status-logs", {"graduate_id": graduate_id})
    return db.query(StatusChangeLog).filter(
        StatusChangeLog.graduate_id == graduate_id
    ).order_by(StatusChangeLog.changed_at.desc()).all()
