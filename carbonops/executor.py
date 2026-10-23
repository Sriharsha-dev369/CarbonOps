"""Runs allowed actions safely: snapshot, apply, health check, roll back.

Deterministic: never imports the LLM client or any agent module (a test checks the source).
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from carbonops import audit as audit_log
from carbonops.models import Action, AuditEvent, Decision, ExecResult, Role
from carbonops.providers.base import CloudProvider

APPROVER_ROLES = ('Approver', 'Admin')
NEVER_EXECUTED = ('DELETE', 'TERMINATE')


def execute(
    action: Action,
    provider: CloudProvider,
    audit: str | Path,
    approved_by: str | None,
    *,
    decision: Decision,
    role: Role | Literal['system'] = 'system',
) -> ExecResult:
    """Execute one action. `audit` is the audit JSONL path.

    Runs only AUTO actions, or APPROVE actions approved by an Approver/Admin; anything else is
    SKIPPED with the reason. KEEP -> SKIPPED (no-op). Otherwise snapshot -> apply -> health check;
    a failed check restores the snapshot (ROLLED_BACK); an error restores if possible (FAILED).
    Audit events: snapshot, apply, health_check, rollback, done (and skipped / failed).
    """
    actor = approved_by or 'system'
    event_role = role if approved_by else 'system'

    def log(kind: str, **payload) -> None:
        audit_log.append(AuditEvent(
            ts=datetime.now(timezone.utc).isoformat(timespec='seconds'), actor=actor, role=event_role, kind=kind,
            payload={'action_id': action.id, 'resource_id': action.resource_id, 'type': action.type, **payload},
        ), audit)

    def skip(reason: str) -> ExecResult:
        log('skipped', reason=reason)
        return ExecResult(action_id=action.id, status='SKIPPED', detail=reason)

    if decision.action_id != action.id:
        return skip('Decision does not belong to this action.')
    if decision.tier == 'BLOCK':
        return skip('Blocked by policy.')
    if decision.tier == 'APPROVE':
        if not approved_by:
            return skip('Needs approval and has none.')
        if role not in APPROVER_ROLES:
            return skip(f'{role} cannot approve; only Approver or Admin.')
    if action.type in NEVER_EXECUTED:
        return skip(f'{action.type} is never executed by CarbonOps.')
    if action.type == 'KEEP':
        return skip('KEEP: nothing to change.')

    snapshot_id = None
    try:
        snapshot_id = provider.snapshot(action.resource_id)
        log('snapshot', snapshot_id=snapshot_id)
        provider.apply(action)
        log('apply', params=action.params)
        health = provider.health_ok(action.resource_id)
        log('health_check', ok=health.ok, latency_ms=health.latency_ms, threshold_ms=health.threshold_ms)
        if not health.ok:
            provider.restore(action.resource_id, snapshot_id)
            detail = (f'Health check failed: {health.latency_ms:.0f} ms > {health.threshold_ms:.0f} ms threshold. '
                      f'Restored from {snapshot_id}.')
            log('rollback', snapshot_id=snapshot_id, reason=detail)
            return ExecResult(action_id=action.id, status='ROLLED_BACK', detail=detail)
        detail = f'Applied; health check {health.latency_ms:.0f} ms ≤ {health.threshold_ms:.0f} ms.'
        log('done', detail=detail)
        return ExecResult(action_id=action.id, status='DONE', detail=detail)
    except Exception as e:
        detail = f'{type(e).__name__}: {e}'
        if snapshot_id:
            try:
                provider.restore(action.resource_id, snapshot_id)
                log('rollback', snapshot_id=snapshot_id, reason=detail)
                detail += f' Restored from {snapshot_id}.'
            except Exception as restore_error:
                detail += f' Restore also failed: {restore_error}'
        log('failed', error=detail)
        return ExecResult(action_id=action.id, status='FAILED', detail=detail)
