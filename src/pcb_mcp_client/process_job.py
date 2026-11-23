"""Supervise a local job, including children detached by a runtime broker.

The marker is a fresh, private input path carried in the actual process command.
Never terminate all Wine/LTspice processes or rely only on the wrapper PID.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import signal
import subprocess
import time


@dataclass(frozen=True)
class Process:
    pid: int
    uid: int
    started: str
    command: str


def snapshot():
    result = subprocess.run(['/bin/ps', '-ww', '-axo', 'pid=,uid=,lstart=,command='],
                            capture_output=True, text=True, timeout=5, check=True)
    rows = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 7)
        if len(parts) != 8:
            continue
        rows.append(Process(int(parts[0]), int(parts[1]), ' '.join(parts[2:7]), parts[7]))
    return rows


def belongs(process, marker):
    text = process.command.replace('\\', '/').lower()
    paths = {os.path.abspath(marker).lower(), str(Path(marker).resolve()).lower()}
    return (process.uid == os.getuid() and process.pid != os.getpid()
            and any(re.search(r'''(?:^|[\s"'])(?:z:)?''' + re.escape(path)
                              + r'''(?=$|[\s"'])''', text) for path in paths))


def stop_marked(marker, *, grace_s=2., list_processes=snapshot, send_signal=os.kill):
    """Check process identity immediately before signalling; report survivors."""
    signals = []
    for sig, wait in ((signal.SIGTERM, grace_s), (signal.SIGKILL, .2)):
        candidates = [p for p in list_processes() if belongs(p, marker)]
        for expected in candidates:
            current = next((p for p in list_processes() if p.pid == expected.pid), None)
            if current != expected:
                continue
            try:
                send_signal(expected.pid, sig)
                signals.append({'pid': expected.pid, 'signal': int(sig)})
            except ProcessLookupError:
                pass
        if candidates:
            time.sleep(wait)
    survivors = [p.pid for p in list_processes() if belongs(p, marker)]
    return {'signals': signals, 'survivors': survivors, 'verified_stopped': not survivors}


def run_job(command, marker, console, *, timeout_s, max_bytes=512*1024*1024, outputs=()):
    if timeout_s <= 0 or max_bytes <= 0:
        raise ValueError('INVALID_JOB_LIMIT')
    # Refuse to launch if we cannot enumerate processes or this input is active.
    if any(belongs(p, marker) for p in snapshot()):
        raise ValueError('JOB_ALREADY_RUNNING')
    began = time.monotonic()
    status, error = 'complete', None
    process = None
    observed = {}
    with Path(console).open('x') as log:
        try:
            process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
            while process.poll() is None:
                for row in snapshot():
                    if belongs(row, marker):
                        observed[(row.pid, row.started)] = row
                if time.monotonic() - began >= timeout_s:
                    status = 'timeout'; break
                if sum(p.stat().st_size for p in outputs if p.exists()) > max_bytes:
                    status = 'output_limit'; break
                time.sleep(.25)
            if status == 'complete' and process.returncode != 0:
                status = 'execution_failed'
        except BaseException as exc:
            status, error = 'execution_failed', repr(exc)
        finally:
            # Runtime servers can parent the simulator outside our process group.
            # First stop exact-marker processes, then clean up our own wrapper group.
            try:
                cleanup = stop_marked(marker)
            except Exception as exc:
                cleanup = {'verified_stopped': False, 'error': repr(exc)}
            if process is not None:
                # Reap an already exited wrapper before attempting group cleanup.
                # macOS can return EPERM for a group whose last member has exited;
                # that is not evidence that the already verified children survived.
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except PermissionError as exc:
                        if process.poll() is None:
                            cleanup.update(verified_stopped=False, group_error=repr(exc))
                        else:
                            cleanup['group_signal_raced_with_exit'] = repr(exc)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    cleanup.update(verified_stopped=False, wrapper_still_running=True)
                # Check again after wrapper teardown for children started by a broker
                # during the first cleanup pass.
                try:
                    final = stop_marked(marker)
                    cleanup.setdefault('signals', []).extend(final['signals'])
                    cleanup['survivors'] = final['survivors']
                    cleanup['verified_stopped'] = cleanup['verified_stopped'] and final['verified_stopped']
                except Exception as exc:
                    cleanup.update(verified_stopped=False, final_check_error=repr(exc))
            if not cleanup['verified_stopped']:
                status = 'cleanup_unverified'
    return dict(status=status, returncode=process.returncode if process else None,
                wrapper_pid=process.pid if process else None,
                observed_processes=[vars(p) for p in observed.values()],
                elapsed_s=time.monotonic()-began, error=error, cleanup=cleanup)
