"""调用方角色定义与请求头解析约定。

生产环境应通过网关注入 ``X-Auth-Role`` 与 ``X-Auth-College-Id``；
缺少有效角色的请求一律视为未认证（401），避免裸奔接口。
"""

import enum


class Role(str, enum.Enum):
    """系统内仅有的两种数据访问角色。"""

    SCHOOL = "school"   # 校级管理员：全量能力
    COLLEGE = "college"  # 学院老师：仅限本学院数据

    @classmethod
    def parse(cls, raw: object) -> "Role | None":
        if isinstance(raw, Role):
            return raw
        if not isinstance(raw, str) or not raw.strip():
            return None
        value = raw.strip().lower()
        for member in cls:
            if member.value == value:
                return member
        return None


#: 识别角色与学院归属的请求头
ROLE_HEADER = "X-Auth-Role"
COLLEGE_HEADER = "X-Auth-College-Id"

#: 允许通过写操作（建档、检测等）的角色。当前仅校级。
WRITE_ROLES = frozenset({Role.SCHOOL})
