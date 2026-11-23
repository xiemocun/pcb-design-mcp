"""Generate both firmware headers from one verified hardware manifest."""
import json
import hashlib
from pathlib import Path
import re


def headers(hardware):
    for key in ('project_id', 'pcb_build_id', 'schematic_build_id'):
        if not re.fullmatch('[0-9a-f]{32}', hardware.get(key, '')):
            raise ValueError('INVALID_HARDWARE_ID: ' + key)
    if type(hardware.get('revision')) is not int or hardware['revision'] < 0:
        raise ValueError('INVALID_HARDWARE_REVISION')
    interface = hardware.get('interface', {})
    if interface.get('kind') != 'split_open_drain':
        raise ValueError('UNSUPPORTED_FIRMWARE_INTERFACE')
    if any(type(value) is not int or value != 1 for value in
           (hardware.get('drive_release_level'), interface.get('drive_release_level'))):
        raise ValueError('UNSUPPORTED_RELEASE_LEVEL')
    safe = {4, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33}
    for key in ('read_gpio', 'drive_gpio'):
        pin = hardware.get(key)
        if type(pin) is not int or pin not in safe or interface.get(key) != 'IO' + str(pin):
            raise ValueError('HARDWARE_GPIO_MISMATCH: ' + key)
    if hardware['read_gpio'] == hardware['drive_gpio']:
        raise ValueError('HARDWARE_GPIO_COLLISION')
    identity = '/* Generated from hardware.json by the firmware build. */\n'
    for macro, key in [('BENCH_PROJECT_ID', 'project_id'), ('BENCH_PCB_BUILD_ID', 'pcb_build_id')]:
        identity += '#define ' + macro + ' ' + json.dumps(hardware[key]) + '\n'
    identity += '#define BENCH_REVISION ' + str(hardware['revision']) + '\n'
    pins = '/* Generated from the verified interface in hardware.json. */\n'
    pins += '#define DRIVE_GPIO GPIO_NUM_' + str(hardware['drive_gpio']) + '\n'
    pins += '#define READ_GPIO GPIO_NUM_' + str(hardware['read_gpio']) + '\n'
    return {'bench_identity.h': identity, 'board_pins.h': pins}


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_record(root, build):
    """Capture the successful build's inputs and outputs, excluding caches."""
    names = ['hardware.json', 'hardware_config.py', 'configure.py', 'platformio.ini',
             'CMakeLists.txt', 'sdkconfig.defaults', 'sdkconfig.esp32wroom']
    names += [str(p.relative_to(root)) for p in sorted((root / 'src').rglob('*')) if p.is_file()]
    return dict(source_files={n: file_hash(root / n) for n in names},
                outputs={n: file_hash(build / n) for n in
                         ('firmware.elf', 'firmware.bin', 'bootloader.bin', 'partitions.bin', 'flasher_args.json')})


def verify_build(root, build):
    """Reject stale source, mismatched headers, or a binary from another build."""
    recorded = json.loads((build / 'firmware-provenance.json').read_text())
    if recorded != build_record(root, build):
        raise ValueError('FIRMWARE_BUILD_STALE_OR_CHANGED')
    hardware = json.loads((root / 'hardware.json').read_text())
    for name, text in headers(hardware).items():
        if (root / 'src' / name).read_text() != text:
            raise ValueError('FIRMWARE_HEADER_MISMATCH')
    binary = (build / 'firmware.bin').read_bytes()
    if binary[176:208].hex() != recorded['outputs']['firmware.elf']:
        raise ValueError('FIRMWARE_ELF_BINARY_MISMATCH')
    for key in ('project_id', 'pcb_build_id'):
        if hardware[key].encode() + b'\0' not in binary:
            raise ValueError('FIRMWARE_BINARY_IDENTITY_MISMATCH')
    return recorded
