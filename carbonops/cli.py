"""Command line: run the pipeline or reset the demo estate.

  uv run python -m carbonops.cli run --client client_a [--approve-all] [--llm]
  uv run python -m carbonops.cli reset --client client_a
"""

import argparse
import sys

from carbonops import calc, config, graph
from carbonops.fmt import inr
from carbonops.moto_runtime import ensure_moto


def _table(rows: list[list[str]]) -> str:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ['  '.join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in rows]
    lines.insert(1, '  '.join('-' * w for w in widths))
    return '\n'.join(lines)


def print_outcome(outcome: graph.Outcome) -> None:
    s = outcome['state']
    findings = {f.resource_id: f for f in s.get('findings', [])}
    decisions = {d.action_id: d for d in s.get('decisions', [])}
    results = {r.action_id: r for r in s.get('results', [])}
    approvals = s.get('approvals', {})
    pending = {p['id'] for p in outcome['pending']}
    rows = [['resource', 'finding', 'action', 'tier', 'result', '₹/month', 'kg CO₂e/month']]
    for a in s.get('actions', []):
        f = findings.get(a.resource_id)
        result = (results[a.id].status if a.id in results else 'awaiting approval' if a.id in pending
                  else 'rejected' if approvals.get(a.id) == 'reject' else '—')
        rows.append([a.resource_id, f.kind if f and a.proposed_by != 'tag' else 'tag request',
                     a.type, decisions[a.id].tier, result, inr(a.inr_month), f'{a.kgco2e_month:.2f}'])
    for r in s.get('resources', []):
        if r.id not in findings:
            rows.append([r.id, '—', '—', '—', 'no action needed', '—', '—'])
    print(_table(rows))

    t = calc.totals(s['resources'], s['findings'], s['actions'], s['decisions'], s['results'], approvals)
    print(f"\nAUTO {t['auto']} · APPROVE {t['approve']} · BLOCK {t['block']} · no finding {t['no_finding']}")
    print(f"Realised: {inr(t['inr_month'])}/month, {t['kgco2e_year']:,.1f} kg CO₂e/year · "
          f"Pending approval: {inr(t['pending_inr_month'])}/month")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m carbonops.cli', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    run_p = sub.add_parser('run', help='run the pipeline for one client')
    run_p.add_argument('--client', required=True, choices=config.list_clients())
    run_p.add_argument('--approve-all', action='store_true', help='approve every pending action as cli-approver')
    run_p.add_argument('--llm', action='store_true', help='let the LLM analyst propose actions')
    reset_p = sub.add_parser('reset', help='rebuild the demo estate and clear the audit log')
    reset_p.add_argument('--client', required=True, choices=config.list_clients())
    args = parser.parse_args(argv)

    ensure_moto()
    if args.command == 'reset':
        graph.reset_demo(args.client)
        print(f'Reset {config.load_policy(args.client).name}: estate re-seeded, audit log cleared.')
        return 0

    outcome = graph.run(args.client, 'Engineer', 'cli', use_llm=args.llm)
    if outcome['pending'] and args.approve_all:
        outcome = graph.resume(outcome['thread_id'], {p['id']: 'approve' for p in outcome['pending']},
                               approver='cli-approver', role='Approver')
    if outcome['state'].get('error'):
        print(outcome['state']['error'], file=sys.stderr)
        return 1
    print(f"{config.load_policy(args.client).name}\n")
    print_outcome(outcome)
    return 0


if __name__ == '__main__':
    sys.exit(main())
