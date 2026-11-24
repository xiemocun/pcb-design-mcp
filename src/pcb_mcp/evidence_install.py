"""Restore the pinned manufacturer evidence into the asset root the program reads.

The destination is ``config.asset_root() / 'evidence-cache'`` — inside
``pcb_mcp/_assets`` for a wheel install, or the checkout root for a source
install — so restored files land exactly where ``parts.py``, ``usb_psd.py``,
``reset_timing.py`` and ``power_startup.py`` look for them.
``PCB_MCP_ASSET_ROOT`` overrides the location.

First-party fact extracts ship in ``catalog/evidence`` and are copied with a
SHA-256 check. Vendor PDFs are downloaded from the primary URLs recorded in
``catalog/parts.json``; the USB-IF snapshots come from the official release
archives. Bytes are accepted only when they match the pinned SHA-256. A
drifted or unreachable document is reported and left uninstalled — the
catalog is never silently repinned.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from . import config, usb_psd

ALLOWED_HOSTS = {
    'www.vishay.com', 'www.kingbrightusa.com', 'www.aosong.com', 'www.ti.com',
    'gct.co', 'www.we-online.com', 'industrial.panasonic.com',
    'www.analog.com', 'www.usb.org',
}
USER_AGENT = 'pcb-design-mcp-evidence-installer'
TIMEOUT = 300

# USB-IF distributes the pinned snapshots inside release archives. The member
# names below were verified byte-for-byte against the pins in usb_psd.SOURCES.
USB_ARCHIVES = (
    ('https://www.usb.org/sites/default/files/usb_20_20250603.zip',
     {'usb20-core.pdf': 'usb_20.pdf',
      'usb20-device-capacitance-ecn.pdf': 'Device Capacitance ECN.pdf'}),
    ('https://www.usb.org/sites/default/files/USB%20Type-C%202.5%20Release%20202603.zip',
     {'usb-type-c-2.5.pdf': 'USB Type-C Spec R2.5 - March 2026.pdf'}),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def requirements() -> list[dict]:
    """Every pinned evidence file, derived from the shipped catalog itself."""
    root = config.asset_root()
    parts = json.loads((root / 'catalog/parts.json').read_text())['parts']
    reqs: list[dict] = []
    seen: dict[str, str] = {}

    def add(file: str, sha: str, kind: str, url: str | None = None):
        if file in seen:
            if seen[file] != sha:
                raise ValueError(f'conflicting pins for {file}')
            return
        seen[file] = sha
        reqs.append(dict(file=file, sha256=sha, kind=kind, url=url))

    for part in parts:
        for source in part['sources']:
            if 'local_snapshot_file' not in source:
                continue
            if source.get('evidence_kind') == 'primary_web_pdf_fact_extract_local_snapshot':
                add(source['local_snapshot_file'], source['snapshot_sha256'], 'extract')
            else:
                add(source['local_snapshot_file'], source['pdf_sha256'], 'vendor', source['url'])
    reset = json.loads((root / 'catalog/reset-reference.json').read_text())
    snap = reset['source_snapshot']
    add(snap['file'], snap['sha256'], 'extract')
    startup = json.loads((root / 'catalog/startup-reference.json').read_text())
    for row in startup['source_snapshots']:
        if row['file'].endswith('.json'):
            add(row['file'], row['sha256'], 'extract')
    for source in usb_psd.SOURCES:
        add(source['local_snapshot_file'], source['pdf_sha256'], 'usb')
    return reqs


def _download(url: str) -> bytes:
    host = urlparse(url).hostname
    if host not in ALLOWED_HOSTS:
        raise ValueError(f'URL host not in the pinned allowlist: {host}')
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def _download_curl(url: str) -> bytes:
    host = urlparse(url).hostname
    if host not in ALLOWED_HOSTS:
        raise ValueError(f'URL host not in the pinned allowlist: {host}')
    curl = shutil.which('curl')
    if not curl:
        raise RuntimeError('curl is not available for the download fallback')
    with tempfile.TemporaryDirectory(prefix='pcb-evidence-') as tmp:
        dest = Path(tmp) / 'download.bin'
        result = subprocess.run(
            [curl, '--fail', '--location', '--silent', '--show-error',
             '--max-time', str(TIMEOUT), '--user-agent', USER_AGENT,
             '--output', str(dest), url],
            capture_output=True, text=True, timeout=TIMEOUT + 30)
        if result.returncode != 0:
            raise RuntimeError(f'curl failed: {result.stderr.strip()[:300]}')
        return dest.read_bytes()


def _download_retry(url: str) -> bytes:
    try:
        return _download(url)
    except Exception:
        pass
    try:
        return _download(url)
    except Exception:
        # Some vendor sites (e.g. Panasonic industrial) stall urllib reads.
        # curl against the same allowlisted URL is the fallback; the pinned
        # SHA-256 check downstream still applies to whatever comes back.
        return _download_curl(url)


def restore(check_only: bool = False, bundled_only: bool = False) -> list[dict]:
    root = config.asset_root()
    cache = root / 'evidence-cache'
    usb_members = {file: (url, member)
                   for url, members in USB_ARCHIVES for file, member in members.items()}
    archives: dict[str, zipfile.ZipFile] = {}
    report = []
    for req in requirements():
        dest = cache / req['file']
        entry = dict(file=req['file'], kind=req['kind'])
        if dest.is_file() and _sha(dest) == req['sha256']:
            entry['status'] = 'present'
        elif check_only:
            entry['status'] = 'missing' if not dest.is_file() else 'hash-mismatch'
        elif req['kind'] == 'extract':
            source = root / 'catalog/evidence' / req['file']
            if not source.is_file():
                entry['status'] = 'bundle-missing'
            elif _sha(source) != req['sha256']:
                entry['status'] = 'bundle-hash-mismatch'
            else:
                cache.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(source.read_bytes())
                entry['status'] = 'restored'
        elif bundled_only:
            entry['status'] = 'skipped-download'
        elif req['kind'] == 'usb':
            url, member = usb_members[req['file']]
            try:
                if url not in archives:
                    archives[url] = zipfile.ZipFile(io.BytesIO(_download_retry(url)))
                names = [n for n in archives[url].namelist() if Path(n).name == member]
                if len(names) != 1:
                    entry.update(status='archive-member-missing', detail=member)
                else:
                    data = archives[url].read(names[0])
                    got = hashlib.sha256(data).hexdigest()
                    if got != req['sha256']:
                        entry.update(status='drift', pinned=req['sha256'], actual=got)
                    else:
                        cache.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(data)
                        entry['status'] = 'restored'
            except Exception as exc:
                entry.update(status='unreachable', detail=f'{type(exc).__name__}: {exc}')
        else:
            try:
                data = _download_retry(req['url'])
                got = hashlib.sha256(data).hexdigest()
                if got != req['sha256']:
                    entry.update(status='drift', pinned=req['sha256'], actual=got)
                else:
                    cache.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    entry['status'] = 'restored'
            except Exception as exc:
                entry.update(status='unreachable', detail=f'{type(exc).__name__}: {exc}')
        report.append(entry)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog='pcb-evidence',
        description='Restore pinned manufacturer evidence into the asset root this install reads.')
    parser.add_argument('--check', action='store_true',
                        help='report status without downloading or writing')
    parser.add_argument('--bundled-only', action='store_true',
                        help='restore only the bundled first-party fact extracts (offline)')
    args = parser.parse_args(argv)
    print('evidence destination:', config.asset_root() / 'evidence-cache')
    report = restore(check_only=args.check, bundled_only=args.bundled_only)
    for entry in report:
        line = f"{entry['status']:20} {entry['file']}"
        if entry['status'] == 'drift':
            line += f"  pinned={entry['pinned'][:12]} actual={entry['actual'][:12]}"
        elif entry.get('detail'):
            line += '  ' + str(entry['detail'])[:160]
        print(line)
    good = {'present', 'restored'}
    ok = sum(entry['status'] in good for entry in report)
    print(f'{ok}/{len(report)} evidence files available')
    if args.bundled_only:
        needed = [entry for entry in report if entry['kind'] == 'extract']
        return 0 if all(entry['status'] in good for entry in needed) else 1
    return 0 if ok == len(report) else 1


if __name__ == '__main__':
    raise SystemExit(main())
