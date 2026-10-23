"""Prove the approval pause/resume works: CARBONOPS_FAKE=1 uv run python -m carbonops.graph_demo"""

import os
import sys

from carbonops import graph


def main() -> int:
    fake = os.environ.get('CARBONOPS_FAKE') == '1'
    if not fake:
        print('note: CARBONOPS_FAKE is not 1, so the real (stub) modules will run')

    outcome = graph.run('client_a', role='Engineer', user='demo-engineer', fake=fake)
    pending = outcome['pending']
    print(f"Paused at approval with {len(pending)} pending action(s):")
    for p in pending:
        print(f"  - {p['id']:<28} {p['type']:<9} ₹{p['inr_month']:>9,.0f}/month  {'; '.join(p['reasons'])}")
    if not pending:
        print('FAIL: expected the run to pause for approvals')
        return 1

    outcome = graph.resume(outcome['thread_id'], {p['id']: 'approve' for p in pending},
                           approver='demo-approver', role='Approver')
    if outcome['pending']:
        print('FAIL: run is still paused after resume')
        return 1

    state = outcome['state']
    print(f"\nResumed and finished with {len(state['results'])} result(s):")
    for r in state['results']:
        print(f"  - {r.action_id:<28} {r.status:<12} {r.detail}")
    t = state['totals']
    print(f"\nTotals: {t['auto']} AUTO, {t['approve']} APPROVE, {t['block']} BLOCK, {t['no_finding']} no finding; "
          f"₹{t['inr_month']:,.0f}/month, {t['kgco2e_year']:,.1f} kg CO₂e/year")
    print(f"Report: {state['report']}\n")
    print('PAUSE/RESUME OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
