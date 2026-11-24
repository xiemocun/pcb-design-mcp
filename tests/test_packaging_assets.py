"""Regression: vendor binaries and downloaded datasheets never enter distributions.

A development checkout may contain evidence-cache/*.pdf and the Freerouting
JAR in vendor/. Building directly from such a tree (``python -m build``)
must still produce a wheel and an sdist without any PDF/JAR, while keeping
the first-party runtime assets: the catalog including the publishable fact
extracts, the router manifest and the reviewer source.
"""
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(
    '.git', '.venv*', 'dist', 'build', '__pycache__', '*.egg-info',
    'outputs', '*.log', '.pytest_cache', 'evidence-cache', '*.jar', '*.pdf')


class PackagingAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for module in ('build', 'setuptools', 'wheel'):
            try:
                __import__(module)
            except ImportError:
                raise unittest.SkipTest(f'{module} is required for the packaging regression')
        cls._tmp = tempfile.TemporaryDirectory(prefix='pcb-packaging-')
        base = Path(cls._tmp.name)
        cls.tree = base / 'tree'
        shutil.copytree(ROOT, cls.tree, ignore=IGNORE, symlinks=True)
        # Plant the exact files a working development checkout would have.
        evidence = cls.tree / 'evidence-cache'
        evidence.mkdir(exist_ok=True)
        (evidence / 'fake-datasheet.pdf').write_bytes(
            b'%PDF-1.4 fake vendor datasheet planted for the packaging regression\n')
        vendor = cls.tree / 'vendor'
        vendor.mkdir(exist_ok=True)
        (vendor / 'freerouting-2.4.1.jar').write_bytes(
            b'PK\x03\x04 fake freerouting jar planted for the packaging regression\n')
        cls.dist = base / 'dist'
        for mode in ('--wheel', '--sdist'):
            result = subprocess.run(
                [sys.executable, '-m', 'build', mode, '--no-isolation',
                 '--outdir', str(cls.dist)],
                cwd=cls.tree, capture_output=True, text=True, timeout=900)
            if result.returncode != 0:
                raise AssertionError(f'python -m build {mode} failed:\n'
                                     f'{result.stdout[-3000:]}\n{result.stderr[-3000:]}')

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _wheel_names(self):
        wheel = next(self.dist.glob('*.whl'))
        with zipfile.ZipFile(wheel) as archive:
            return archive.namelist()

    def _sdist_names(self):
        sdist = next(self.dist.glob('*.tar.gz'))
        with tarfile.open(sdist) as archive:
            return archive.getnames()

    def test_wheel_excludes_vendor_binaries_and_datasheets(self):
        names = self._wheel_names()
        self.assertFalse([n for n in names if n.lower().endswith(('.pdf', '.jar'))],
                         'wheel must not ship PDFs or JARs even when present in the source tree')
        self.assertFalse([n for n in names if 'evidence-cache' in n])
        self.assertFalse([n for n in names if 'freerouting' in n and n.endswith('.jar')])

    def test_wheel_keeps_first_party_runtime_assets(self):
        names = set(self._wheel_names())
        required = [
            'pcb_mcp/_assets/catalog/parts.json',
            'pcb_mcp/_assets/catalog/component-heights.json',
            'pcb_mcp/_assets/catalog/reset-reference.json',
            'pcb_mcp/_assets/catalog/startup-reference.json',
            'pcb_mcp/_assets/catalog/symbols/PCB_MCP.kicad_sym',
            'pcb_mcp/_assets/runtime/router.json',
            'pcb_mcp/_assets/reviewer/Reviewer.swift',
        ]
        for extract in (ROOT / 'catalog/evidence').glob('*.json'):
            required.append(f'pcb_mcp/_assets/catalog/evidence/{extract.name}')
        for name in required:
            self.assertIn(name, names)
        footprints = [n for n in names if n.endswith('.kicad_mod')]
        self.assertGreaterEqual(len(footprints), 4)

    def test_sdist_excludes_vendor_binaries_and_datasheets(self):
        names = self._sdist_names()
        self.assertFalse([n for n in names if n.lower().endswith(('.pdf', '.jar'))],
                         'sdist must not ship PDFs or JARs even when present in the source tree')
        self.assertFalse([n for n in names if '/evidence-cache/' in n])

    def test_sdist_keeps_first_party_sources(self):
        names = self._sdist_names()
        required = [
            'catalog/parts.json',
            'catalog/evidence/avx-c0g-web-extract.json',
            'install_evidence.py', 'install_router.py',
            'scripts/stdio_smoke.py',
            'src/pcb_mcp/server.py', 'src/pcb_mcp/evidence_install.py',
            'src/pcb_mcp/router_install.py',
            'tests/test_rpc.py', 'runtime/router.json', 'reviewer/Reviewer.swift',
            'DEPENDENCIES.md', 'setup.py', 'pyproject.toml', 'MANIFEST.in',
        ]
        for name in required:
            self.assertTrue(any(n.endswith('/' + name) for n in names),
                            f'sdist missing first-party file: {name}')


if __name__ == '__main__':
    unittest.main()
