from typing import Optional, List
from .common import BaseSchema, TimestampSchema
from app.models import UserRole


class UserAccountBase(BaseSchema):
    username: str
    display_name: str
    role: UserRole
    is_active: bool = True


class UserAccountCreate(UserAccountBase):
    api_token: Optional[str] = None
    college_ids: List[int] = []


class UserAccount(UserAccountBase, TimestampSchema):
    id: int
    college_ids: List[int] = []


class UserCollegeGrantBase(BaseSchema):
    user_id: int
    college_id: int
    granted_by: Optional[str] = None


class UserCollegeGrant(UserCollegeGrantBase, TimestampSchema):
    id: int


class UserGrantUpdate(BaseSchema):
    college_ids: List[int]
