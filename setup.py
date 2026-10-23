"""Compatibility shim.

Packaging metadata lives in ``pyproject.toml`` (Sentinel 2.0).  The legacy
flat-module install list was removed so there is exactly one console entry
point: ``project-sentinel = sentinel2.cli:main``.
"""

from setuptools import setup

setup()
