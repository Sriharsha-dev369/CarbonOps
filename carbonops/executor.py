"""Runs allowed actions safely: snapshot, apply, health check, roll back. STUB: written on the day."""

from pathlib import Path
from typing import Literal

from carbonops.models import Action, Decision, ExecResult, Role
from carbonops.providers.base import CloudProvider as Provider


def execute(
    action: Action,
    provider: Provider,
    audit: str | Path,
    approved_by: str | None,
    *,
    decision: Decision,
    role: Role | Literal['system'] = 'system',
) -> ExecResult:
    """Execute one action. `audit` is the audit JSONL path; write events with carbonops.audit.append.

    Allowed only when decision.tier == 'AUTO', or decision.tier == 'APPROVE' and `approved_by`
    is set and `role` is Approver or Admin. Anything else (including BLOCK) is never run:
    return SKIPPED with the reason in `detail`.

    KEEP -> SKIPPED (no-op).
    Otherwise: snapshot -> apply -> health_ok.
      health_ok False -> restore, status ROLLED_BACK.
      any exception   -> restore if a snapshot exists, status FAILED, detail = error.
      else            -> DONE.
    Write an audit event for each step (kind 'execution', or 'rollback' for the restore),
    actor = approved_by or 'system', role = role.
    """
    raise NotImplementedError
