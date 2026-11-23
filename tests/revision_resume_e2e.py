"""Lose the journal acknowledgement of a real mechanical edit, then resume."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from pcb_mcp.config import data_dir
from pcb_mcp_client.journal import Journal, save
from pcb_mcp_client.run_plan import execute


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--requirement', type=Path, required=True)
    parser.add_argument('--base-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    run = root / 'run'
    finish = Journal.finish

    def interrupt(journal, entry, response):
        if entry['tool'] == 'board_constraints_set' and not response.get('isError'):
            raise asyncio.CancelledError('test: server committed the revision edit before acknowledgement')
        return finish(journal, entry, response)

    with patch.object(Journal, 'finish', interrupt):
        try:
            asyncio.run(execute(args.plan, args.requirement, run, base_run=args.base_run))
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError('No committed edit was interrupted')

    interrupted = read(run / 'run.json')
    assert interrupted['status'] == 'interrupted'
    project = data_dir() / interrupted['project_id']
    state = read(project / 'state.json')
    journal = read(run / 'journal.json')
    pending = journal['calls'][-1]
    key = pending['arguments']['idempotency_key']
    assert pending['tool'] == 'board_constraints_set' and 'response' not in pending
    assert state['revision'] == state['idempotency'][key]['result']['revision'] == 1
    assert state['provenance']['parent_project_id'] == interrupted['parent']['project_id']
    assert not state['pcb_builds']
    save(root / 'interrupted-result.json', interrupted)
    save(root / 'interrupted-journal.json', journal)
    save(root / 'committed-state-before-resume.json', state)
    fork_ledger = (data_dir() / 'fork_requests.json').read_bytes()

    command = [sys.executable, '-m', 'pcb_mcp_client.run_plan', str(args.plan),
               '--requirement', str(args.requirement), '--base-run', str(args.base_run),
               '--output', str(run), '--resume']
    with (root / 'resume.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=360, check=True)
    result = read(run / 'run.json')
    recovered = read(project / 'state.json')
    replayed = read(run / 'journal.json')
    assert result['status'] == 'cad_checks_passed' and result['software_acceptance'] == 'pass'
    assert result['project_id'] == interrupted['project_id']
    assert result['revision'] == recovered['revision'] == 2
    assert recovered['idempotency'][key] == state['idempotency'][key]
    assert sum(row['tool'] == 'project_fork' for row in replayed['calls']) == 1
    assert (data_dir() / 'fork_requests.json').read_bytes() == fork_ledger
    save(root / 'verification.json', dict(
        passed=True, project_id=result['project_id'], parent=result['parent'],
        interrupted_committed_revision=1, delivered_revision=2, edit_replayed_once=True,
        actual_placements=read(run / 'revision.json')['actual_placements'],
        physical_verified=False,
    ))


if __name__ == '__main__':
    main()
