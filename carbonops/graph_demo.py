"""Prove the approval pause/resume works end to end: uv run python -m carbonops.graph_demo

Runs the real graph for Acme Retail against an in-process AWS emulator, in a throwaway folder.
"""

import os
import sys
import tempfile

from moto import mock_aws

from carbonops import audit, graph
from carbonops.fmt import inr


def main() -> int:
    workdir = tempfile.mkdtemp(prefix='carbonops-demo-')
    os.environ.pop('AWS_ENDPOINT_URL', None)
    os.environ.update(AWS_ACCESS_KEY_ID='testing', AWS_SECRET_ACCESS_KEY='testing', AWS_DEFAULT_REGION='ap-south-1',
                      CARBONOPS_STATE_DIR=workdir, CARBONOPS_AUDIT_DIR=workdir)
    with mock_aws():
        outcome = graph.run('client_a', role='Engineer', user='demo-engineer')
        pending = outcome['pending']
        print(f'Paused at approval with {len(pending)} pending action(s):')
        for p in pending:
            print(f"  - {p['id']:<26} {p['type']:<9} {inr(p['inr_month']):>9}/month  {p['reasons'][0]}")
        if not pending:
            print('FAIL: expected the run to pause for approvals', outcome['state'].get('error', ''))
            return 1

        outcome = graph.resume(outcome['thread_id'], {p['id']: 'approve' for p in pending},
                               approver='demo-approver', role='Approver')
        state = outcome['state']
        if outcome['pending'] or state.get('error'):
            print('FAIL:', state.get('error') or 'run is still paused after resume')
            return 1

        print(f"\nResumed and finished with {len(state['results'])} result(s):")
        for r in state['results']:
            print(f'  - {r.action_id:<26} {r.status:<12} {r.detail}')
        print(f"\n{state['report']}\n")
        print('Audit chain intact:', audit.verify(state['audit_path']) == (True, None))
    print('PAUSE/RESUME OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
