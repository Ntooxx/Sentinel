from pathlib import Path

from setuptools import setup


README = Path(__file__).with_name("README.md").read_text(encoding="utf-8")


setup(
    name="sentinel-agent",
    version="1.1.0",
    description="Autonomous project monitor, auditor, and suggestion engine",
    long_description=README,
    long_description_content_type="text/markdown",
    py_modules=[
        "sentinel",
        "sentinel_mcp",
        "adapters",
        "auditor",
        "classify",
        "graph",
        "knowledge",
        "monitor",
        "reporter",
        "retriever",
        "suggester",
        "utils",
        "verifier",
    ],
    package_dir={"": "src"},
    python_requires=">=3.8",
    entry_points={
        "console_scripts": [
            "project-sentinel=sentinel:main",
        ]
    },
)
