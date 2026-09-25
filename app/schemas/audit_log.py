from typing import Optional
from datetime import datetime
from .common import BaseSchema


class AuditLog(BaseSchema):
    id: int
    user_id: Optional[int]
    username: str
    role: str
    action: str
    resource: str
    params: Optional[str]
    decision: str
    reason: Optional[str]
    created_at: datetime
