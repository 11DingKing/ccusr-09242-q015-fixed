"""就业成效分析的应用服务。"""

from .cohort_scope import CohortMember, CohortRule, apply_cohort_rule, compare_cohorts
from .report_snapshot import ReportSnapshot, SnapshotStore, build_snapshot
from .workflow_rules import Action, CaseState, WorkflowDecision, decide_action
from .access_control import (
    AccessContext,
    AccessDeniedError,
    DENIED_MESSAGE,
    assert_college_allowed,
    assert_graduate_allowed,
    assert_micro_major_allowed,
    assert_warning_allowed,
    build_access_context,
    combine_scopes,
    graduate_scope_condition,
    resolve_college_scope,
    scoped_micro_major_ids,
    warning_scope_condition,
)

__all__ = [
    "Action",
    "CaseState",
    "CohortMember",
    "CohortRule",
    "ReportSnapshot",
    "SnapshotStore",
    "WorkflowDecision",
    "apply_cohort_rule",
    "build_snapshot",
    "compare_cohorts",
    "decide_action",
    "AccessContext",
    "AccessDeniedError",
    "DENIED_MESSAGE",
    "assert_college_allowed",
    "assert_graduate_allowed",
    "assert_micro_major_allowed",
    "assert_warning_allowed",
    "build_access_context",
    "combine_scopes",
    "graduate_scope_condition",
    "resolve_college_scope",
    "scoped_micro_major_ids",
    "warning_scope_condition",
]
