"""Firmware identity and GPIO consistency; no physical-board assertions."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from pcb_mcp_client.firmware import checked_files, interface_pins
from pcb_mcp_client.transient import sha

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hardware_config', ROOT / 'firmware/am2302/hardware_config.py')
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class FirmwareBindingTests(unittest.TestCase):
    def setUp(self):
        self.hardware = dict(project_id='a' * 32, pcb_build_id='b' * 32,
                             schematic_build_id='c' * 32, revision=3,
                             read_gpio=27, drive_gpio=26, drive_release_level=1,
                             interface=dict(kind='split_open_drain', read_gpio='IO27',
                                            drive_gpio='IO26', drive_release_level=1))

    def test_both_headers_follow_changed_board_and_gpio(self):
        h = self.hardware
        h.update(project_id='d' * 32, read_gpio=32)
        h['interface']['read_gpio'] = 'IO32'
        result = config.headers(h)
        self.assertIn('"' + 'd' * 32 + '"', result['bench_identity.h'])
        self.assertIn('READ_GPIO GPIO_NUM_32', result['board_pins.h'])
        self.assertNotIn('GPIO_NUM_27', result['board_pins.h'])

    def test_stale_gpio_rejected(self):
        self.hardware['read_gpio'] = 32
        with self.assertRaisesRegex(ValueError, 'HARDWARE_GPIO_MISMATCH'):
            config.headers(self.hardware)

    def test_gpio_collision_rejected(self):
        self.hardware['read_gpio'] = 26
        self.hardware['interface']['read_gpio'] = 'IO26'
        with self.assertRaisesRegex(ValueError, 'HARDWARE_GPIO_COLLISION'):
            config.headers(self.hardware)

    def test_unsafe_or_noninteger_gpio_rejected(self):
        for pin in (0, 6, 34, True, '27'):
            h = copy.deepcopy(self.hardware)
            h['read_gpio'] = pin
            h['interface']['read_gpio'] = 'IO' + str(pin)
            with self.assertRaises(ValueError):
                config.headers(h)

    def test_invalid_identity_and_revision_rejected(self):
        for key, value in [('project_id', '"\n#error bad'), ('revision', True), ('revision', -1)]:
            h = dict(self.hardware, **{key: value})
            with self.assertRaises(ValueError):
                config.headers(h)

    def test_unsupported_interface_or_polarity_rejected(self):
        for key, value in [('kind', 'txs0102'), ('drive_release_level', 0)]:
            h = copy.deepcopy(self.hardware)
            h['interface'][key] = value
            with self.assertRaises(ValueError):
                config.headers(h)

    def test_failed_or_ambiguous_interface_cannot_bind(self):
        good = dict(verdict='pass', modules=[self.hardware['interface']], findings=[])
        self.assertEqual(interface_pins(good)['drive_gpio'], 26)
        for report in [dict(good, verdict='incomplete'), dict(good, modules=[]),
                       dict(good, modules=good['modules'] * 2),
                       dict(good, findings=[dict(severity='error')])]:
            with self.assertRaises(ValueError):
                interface_pins(report)

    def test_missing_failed_or_changed_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            file = folder / 'drc.json'
            file.write_text('{"verdict":"pass"}')
            check = dict(verdict='pass', files={'drc.json': sha(file)})
            checked_files(folder, check, 'pass', 'drc')
            for bad in [{}, dict(check, verdict='fail'), dict(check, files={})]:
                with self.assertRaisesRegex(ValueError, 'EVIDENCE_REQUIRED'):
                    checked_files(folder, bad, 'pass', 'drc')
            file.write_text('{"verdict":"fail"}')
            with self.assertRaisesRegex(ValueError, 'EVIDENCE_CHANGED'):
                checked_files(folder, check, 'pass', 'drc')

    def test_evidence_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'INVALID_EVIDENCE_PATH'):
                checked_files(Path(directory), dict(verdict='pass', files={'../other': 'a' * 64}), 'pass', 'drc')

    def test_successful_build_record_rejects_later_source_or_binary_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / 'build'
            build.mkdir()
            (root / 'src').mkdir()
            for name in ('hardware_config.py', 'configure.py', 'platformio.ini', 'CMakeLists.txt',
                         'sdkconfig.defaults', 'sdkconfig.esp32wroom'):
                (root / name).write_text('fixture')
            (root / 'hardware.json').write_text(json.dumps(self.hardware))
            for name, text in config.headers(self.hardware).items():
                (root / 'src' / name).write_text(text)
            (root / 'src/main.c').write_text('fixture source')
            (build / 'firmware.elf').write_bytes(b'fixture ELF')
            binary = bytes(176) + bytes.fromhex(sha(build / 'firmware.elf'))
            binary += self.hardware['project_id'].encode() + b'\0' + self.hardware['pcb_build_id'].encode() + b'\0'
            (build / 'firmware.bin').write_bytes(binary)
            for name in ('bootloader.bin', 'partitions.bin', 'flasher_args.json'):
                (build / name).write_bytes(b'fixture')
            record = config.build_record(root, build)
            (build / 'firmware-provenance.json').write_text(json.dumps(record))
            config.verify_build(root, build)
            for file in (root / 'src/main.c', build / 'firmware.bin', root / 'hardware.json'):
                original = file.read_bytes()
                file.write_bytes(original + b' ')
                with self.assertRaisesRegex(ValueError, 'FIRMWARE_BUILD_STALE_OR_CHANGED'):
                    config.verify_build(root, build)
                file.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
