"""Bundle finite runtime assets; project data, caches and keys are never packaged."""
from pathlib import Path
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithAssets(build_py):
    """Package only first-party runtime assets.

    Vendor binaries (the Freerouting JAR) and downloaded manufacturer
    datasheets are NEVER packaged, even when a development checkout has
    them in vendor/ or evidence-cache/. They are restored at install time
    by ``pcb-router`` / ``pcb-evidence`` from their pinned upstream sources.
    """
    def run(self):
        super().run()
        root = Path(__file__).resolve().parent
        destination = Path(self.build_lib) / 'pcb_mcp/_assets'
        source = root / 'catalog'
        if source.is_dir():
            shutil.copytree(source, destination / 'catalog', dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
        for name in ('runtime/router.json', 'reviewer/Reviewer.swift'):
            source = root / name
            if not source.is_file():
                continue
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


setup(cmdclass={'build_py': BuildWithAssets})
