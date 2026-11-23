"""Bundle finite runtime assets; project data, caches and keys are never packaged."""
from pathlib import Path
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithAssets(build_py):
    def run(self):
        super().run()
        root = Path(__file__).resolve().parent
        destination = Path(self.build_lib) / 'pcb_mcp/_assets'
        for directory in ('catalog', 'evidence-cache'):
            source = root / directory
            if not source.is_dir():
                # Datasheets are fetched from the vendor, not redistributed here.
                continue
            shutil.copytree(source, destination / directory, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
        for name in ('vendor/freerouting-2.4.1.jar', 'runtime/router.json', 'reviewer/Reviewer.swift'):
            source = root / name
            if not source.is_file():
                # The router JAR is downloaded from upstream; see DEPENDENCIES.md.
                continue
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


setup(cmdclass={'build_py': BuildWithAssets})
