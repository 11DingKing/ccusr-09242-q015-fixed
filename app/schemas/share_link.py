from typing import Optional
from .common import BaseSchema, TimestampSchema


class ShareLinkCreate(BaseSchema):
    title: str
    report_type: str
    college_id: Optional[int] = None
    micro_major_id: Optional[int] = None
    graduation_year: Optional[int] = None


class ShareLink(ShareLinkCreate, TimestampSchema):
    id: int
    token: str
    created_by: int
    is_revoked: bool
