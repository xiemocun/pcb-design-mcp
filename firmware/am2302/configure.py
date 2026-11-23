"""Use bundled IDF components in both app and bootloader CMake subprocesses."""
import os
import json
import sys
from pathlib import Path

os.environ['IDF_COMPONENT_MANAGER'] = '0'

Import('env')
root=Path(env.subst('$PROJECT_DIR'))
hardware=json.loads((root/'hardware.json').read_text())
sys.path.insert(0,str(root))
from hardware_config import headers,build_record
# Validate all fields before writing either generated header.
for name,header in headers(hardware).items():
    target=root/'src'/name
    if not target.exists() or target.read_text()!=header:target.write_text(header)

def record_success(source,target,env):
    build=Path(env.subst('$BUILD_DIR'))
    (build/'firmware-provenance.json').write_text(json.dumps(build_record(root,build),indent=2)+'\n')

# Only a successful image action records provenance. A failed build leaves an old
# record, which verify_build rejects against the changed inputs/outputs.
env.Depends('$BUILD_DIR/${PROGNAME}.bin',[str(root/n) for n in ('configure.py','hardware_config.py','hardware.json')])
env.AddPostAction('$BUILD_DIR/${PROGNAME}.bin',record_success)
