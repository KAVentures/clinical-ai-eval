"""Setuptools build hook for runtime data used by installed Clinical-AI-Eval wheels.

The runtime resolves immutable family/config/prompt/schema data from caeval/_data
when it is not running from a source checkout. These files live at repository root,
so the wheel build must explicitly mirror them into the package build directory.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

ROOT = Path(__file__).resolve().parent


class BuildPyWithRuntimeData(_build_py):
    def run(self):
        super().run()
        dest = Path(self.build_lib) / "caeval" / "_data"
        dest.mkdir(parents=True, exist_ok=True)

        shutil.copy2(ROOT / "selection_rules.yaml", dest / "selection_rules.yaml")

        specs = [
            ("tests", "*.yaml"),
            ("configs", "*.toml"),
            ("prompts", "*.txt"),
            ("schemas", "*.json"),
        ]
        for dirname, pattern in specs:
            src_root = ROOT / dirname
            if not src_root.exists():
                continue
            for src in src_root.rglob(pattern):
                rel = src.relative_to(ROOT)
                out = dest / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, out)


setup(cmdclass={"build_py": BuildPyWithRuntimeData})
