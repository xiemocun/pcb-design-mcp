"""Install the pinned Freerouting JAR where ``pcb_service`` actually reads it.

The destination is ``config.asset_root() / 'vendor' / freerouting-<version>.jar``
— inside ``pcb_mcp/_assets`` for a wheel install, or the checkout root for a
source install. ``PCB_MCP_ASSET_ROOT`` overrides the location; ``PCB_MCP_ROUTER``
overrides the JAR path at run time.

The official GitHub release URL comes from the bundled ``runtime/router.json``.
``--url`` accepts a mirror for slow networks; the pinned SHA-256 and the ZIP
CRC check are enforced regardless of the source. Java is not bundled: install
a Java 25 runtime separately (see DEPENDENCIES.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

from . import config


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(url: str | None = None, check_only: bool = False) -> dict:
    root = config.asset_root()
    manifest = json.loads((root / 'runtime/router.json').read_text())
    pin = config.ROUTER_SHA256
    url = url or manifest['source_url']
    dest = root / 'vendor' / f'freerouting-{config.ROUTER_VERSION}.jar'
    if dest.is_file() and _sha(dest) == pin:
        return dict(status='present', path=str(dest), sha256=pin)
    if check_only:
        return dict(status='missing', path=str(dest), pinned_sha256=pin)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pending = dest.with_suffix('.jar.pending')
    request = urllib.request.Request(url, headers={'User-Agent': 'pcb-design-mcp-router-installer'})
    with urllib.request.urlopen(request, timeout=300) as response, pending.open('wb') as out:
        shutil.copyfileobj(response, out)
    got = _sha(pending)
    if got != pin:
        pending.unlink()
        raise SystemExit(f'Freerouting download does not match the pinned SHA-256 '
                         f'(pinned {pin}, got {got}). Manual review required; no silent repinning.')
    with zipfile.ZipFile(pending) as archive:
        bad = archive.testzip()
        if bad:
            pending.unlink()
            raise SystemExit(f'Freerouting JAR failed its ZIP CRC check: {bad}')
    pending.replace(dest)
    record = dict(manifest, installed_path=str(dest), verified_sha256=got,
                  license='GPL-3.0',
                  source_code='https://github.com/freerouting/freerouting/tree/v2.4.1',
                  digest_basis='Pinned after authenticated HTTPS release download, full ZIP CRC validated; '
                               'not a publisher-supplied checksum',
                  java=dict(required_major=25,
                            note='Install a Java 25 runtime separately; see DEPENDENCIES.md.'))
    (dest.parent / 'engine-java.json').write_text(json.dumps(record, indent=2) + '\n')
    return dict(status='restored', path=str(dest), sha256=got)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog='pcb-router',
        description='Install the pinned Freerouting JAR into the asset root this install reads.')
    parser.add_argument('--url',
                        help='override the download URL (e.g. a mirror); the SHA-256 pin is still enforced')
    parser.add_argument('--check', action='store_true',
                        help='report status without downloading')
    args = parser.parse_args(argv)
    result = install(url=args.url, check_only=args.check)
    print(json.dumps(result, indent=1))
    return 0 if result['status'] in ('present', 'restored') else 1


if __name__ == '__main__':
    raise SystemExit(main())
