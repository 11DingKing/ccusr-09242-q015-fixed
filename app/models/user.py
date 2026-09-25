from datetime import datetime

from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Enum, Text, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship

from .base import Base, TimestampMixin
from .enums import UserRole


class UserAccount(Base, TimestampMixin):
    """调用方账号。学院角色的数据可见范围由授权记录决定。"""

    __tablename__ = "user_accounts"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, comment="登录名")
    display_name = Column(String(100), nullable=False, comment="显示名称")
    role = Column(Enum(UserRole), nullable=False, comment="角色")
    api_token = Column(String(64), unique=True, nullable=False, index=True, comment="访问令牌")
    is_active = Column(Boolean, default=True, nullable=False, comment="是否启用")

    college_grants = relationship("UserCollegeGrant", back_populates="user", cascade="all, delete-orphan")
    share_links = relationship("ReportShareLink", back_populates="creator")


class UserCollegeGrant(Base, TimestampMixin):
    """学院角色被授权访问的学院范围，可随管理决策增删。"""

    __tablename__ = "user_college_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "college_id", name="uq_user_college_grant"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("user_accounts.id"), nullable=False, index=True, comment="用户ID")
    college_id = Column(Integer, ForeignKey("colleges.id"), nullable=False, comment="授权学院ID")
    granted_by = Column(String(50), comment="授权人")

    user = relationship("UserAccount", back_populates="college_grants")
    college = relationship("College")


class ReportShareLink(Base, TimestampMixin):
    """报告分享链接。只保存报告定义，访问时按当前权限实时校验并重新计算。"""

    __tablename__ = "report_share_links"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), unique=True, nullable=False, index=True, comment="分享令牌")
    title = Column(String(100), nullable=False, comment="报告标题")
    report_type = Column(String(20), nullable=False, comment="报告类型: college/micro_major/year")
    college_id = Column(Integer, ForeignKey("colleges.id"), nullable=True, comment="报告学院ID")
    micro_major_id = Column(Integer, ForeignKey("micro_majors.id"), nullable=True, comment="报告微专业ID")
    graduation_year = Column(Integer, nullable=True, comment="报告届次")
    created_by = Column(Integer, ForeignKey("user_accounts.id"), nullable=False, comment="创建人ID")
    is_revoked = Column(Boolean, default=False, nullable=False, comment="是否已撤销")

    creator = relationship("UserAccount", back_populates="share_links")


class AuditLog(Base):
    """访问审计。只记录调用参数与判定结果，绝不写入统计数值。"""

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("user_accounts.id"), nullable=True, comment="调用方用户ID")
    username = Column(String(50), nullable=False, comment="调用方登录名")
    role = Column(String(20), nullable=False, comment="调用方角色")
    action = Column(String(50), nullable=False, comment="操作标识")
    resource = Column(String(200), nullable=False, comment="访问的资源路径")
    params = Column(Text, comment="请求参数JSON")
    decision = Column(String(10), nullable=False, comment="判定: allowed/denied")
    reason = Column(String(200), comment="判定原因")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="发生时间")
