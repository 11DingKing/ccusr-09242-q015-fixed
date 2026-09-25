"""越权访问审计。

审计条目只保存定位事件所需的安全元数据（角色、路径、参数名、资源类型与ID、
原因码），刻意不记录请求体或任何统计响应内容，确保越权请求不会在审计记录中
沉淀敏感数字。
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from threading import Lock
from typing import Optional


@dataclass(frozen=True)
class AuditEntry:
    timestamp: str
    role: str
    method: str
    path: str
    reason: str
    resource_type: str = ""
    resource_id: Optional[int] = None
    param: str = ""
    college_ids: tuple[int, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["college_ids"] = list(self.college_ids)
        return data


class AuditLog:
    """进程内越权审计日志（持久化适配层的参考实现）。"""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._lock = Lock()

    def record(
        self,
        *,
        role: str,
        method: str,
        path: str,
        reason: str,
        resource_type: str = "",
        resource_id: Optional[int] = None,
        param: str = "",
        college_ids: tuple[int, ...] = (),
    ) -> AuditEntry:
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            role=role,
            method=method.upper(),
            path=path,
            reason=reason,
            resource_type=resource_type,
            resource_id=resource_id,
            param=param,
            college_ids=tuple(sorted(college_ids)),
        )
        with self._lock:
            self._entries.append(entry)
        return entry

    def entries(self) -> tuple[AuditEntry, ...]:
        with self._lock:
            return tuple(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


#: 全局审计日志实例
audit_log = AuditLog()
