"""Use the installed ESP-IDF 5.5 Xtensa toolchain directly (no unused ULP).
No flashing. Outputs stay in this repository. A and B share provisioned/pair.h.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parent
repo=root.parent.parent
def inputs():
    paths=[p for p in root.rglob('*') if p.is_file() and not any(n in p.parts for n in ('components','.pio','__pycache__'))
           and (p.suffix in ('.c','.h','.py','.ini','.csv') or p.name in ('CMakeLists.txt','sdkconfig.defaults'))]
    paths += list((root/'components').glob('*/CMakeLists.txt'))
    paths += [repo/'work/riding-intercom/sources/manifest.json']
    return {str(p.relative_to(repo)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
parser=argparse.ArgumentParser()
parser.add_argument('--unit', choices=['a','b'], required=True)
args=parser.parse_args()
subprocess.run([sys.executable,str(root/'prepare.py')],check=True)
before=inputs()
core=(repo/'../../work/interface-redesign/platformio').resolve()
packages=core/'packages'
python=core/'penv/.espidf-5.5.0/bin/python'
idf=packages/'framework-espidf'
tools=['tool-cmake','tool-ninja','toolchain-xtensa-esp-elf']
env={**os.environ,'IDF_PATH':str(idf),'IDF_COMPONENT_MANAGER':'0',
     'ESP_IDF_VERSION':'5.5.0',
     'PYTHONPATH':os.pathsep.join([str(packages/'tool-esptoolpy'),str(packages/'tool-esptoolpy/_contrib'),str(core.parent/'build-env/lib/python3.12/site-packages'),str((repo/'.venv/lib/python3.12/site-packages').resolve())]),
     'IDF_PYTHON_ENV_PATH':str(python.parent.parent),
     'PATH':os.pathsep.join([str(python.parent),str(packages/'tool-ninja')]+[str(packages/t/'bin') for t in tools]+[os.environ['PATH']])}
build=repo/'work/riding-intercom'/('idf-'+args.unit)
# Match PlatformIO's direct CMake integration. Its Python environment contains
# the build modules, not every idf.py console/monitor package; no ULP is enabled.
cmd=[str(packages/'tool-cmake/bin/cmake'),'-S',str(root),'-B',str(build),'-G','Ninja',
     '-DPYTHON='+str(python),'-DPYTHON_DEPS_CHECKED=1',
     '-DIDF_TARGET=esp32s3','-DRIDE_ROLE_AP='+str(int(args.unit=='a')),
     '-DSDKCONFIG='+str(build/'sdkconfig')]
rc=subprocess.call(cmd,cwd=root,env=env)
if rc: sys.exit(rc)
rc=subprocess.call([str(packages/'tool-ninja/ninja'),'-C',str(build),'-j4'],env=env)
if rc: sys.exit(rc)
assert before==inputs(), 'Build inputs changed during compilation; rebuild required'
files=['riding_intercom.bin','riding_intercom.elf','bootloader/bootloader.bin',
       'partition_table/partition-table.bin','srmodels/srmodels.bin','sdkconfig','flasher_args.json']
record=dict(unit=args.unit,target='esp32s3',idf='5.5.0',built=True,flashed=False,
            physical_verified=False,inputs=before,
            outputs={n:dict(sha256=hashlib.sha256((build/n).read_bytes()).hexdigest(),bytes=(build/n).stat().st_size) for n in files})
(build/'build-record.json').write_text(json.dumps(record,indent=2)+'\n')
print('Verified build record:',build/'build-record.json')
