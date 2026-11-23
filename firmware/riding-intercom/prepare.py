"""Prepare pinned local dependencies and a unique pair; never prints the key.

Use the source archive hashes in work/riding-intercom/sources/manifest.json.
This does not download, flash, or change any other project's toolchain.
"""
import hashlib
import json
import os
import secrets
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
WORK = REPO / 'work/riding-intercom'
NAMES = ('esp_codec_dev', 'esp_audio_codec', 'esp-sr', 'cjson', 'dl_fft', 'esp-dl', 'esp_new_jpeg')

def main():
    manifest = json.loads((WORK / 'sources/manifest.json').read_text())
    (ROOT / 'components').mkdir(exist_ok=True)
    for name in NAMES:
        archive = WORK / 'sources' / (name + '.zip')
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest[archive.name]['sha256'], name
        target = WORK / 'deps' / name
        if not target.exists():
            with zipfile.ZipFile(archive) as z:
                assert all('..' not in Path(n).parts and not n.startswith('/') for n in z.namelist())
                z.extractall(WORK / 'deps')
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                if not entry.is_dir():
                    local = WORK / 'deps' / entry.filename
                    assert local.read_bytes() == z.read(entry), 'Modified dependency: ' + entry.filename
        link = ROOT / 'components' / ('espressif__' + name)
        if not link.exists(): link.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)
    provisioned = ROOT / 'provisioned'
    provisioned.mkdir(exist_ok=True, mode=0o700)
    pair = provisioned / 'pair.h'
    if not pair.exists():
        with pair.open('x') as f:
            f.write('#pragma once\n#define RIDE_SSID "Ride-' + secrets.token_hex(4) + '"\n')
            f.write('#define RIDE_PSK "' + secrets.token_hex(16) + '"\n')
        pair.chmod(0o600)
    print('Pinned components prepared. Existing pair identity preserved; credentials not displayed.')

if __name__ == '__main__': main()
