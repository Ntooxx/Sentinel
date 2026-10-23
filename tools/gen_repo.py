#!/usr/bin/env python3
"""Generate a deterministic synthetic repository for scale benchmarks."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def generate(out: Path, total_files: int, seed: int = 7, force: bool = False) -> int:
    if out.exists() and any(out.iterdir()):
        if not force:
            raise SystemExit(f"output directory is not empty: {out} (use --force)")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    _ = seed  # kept for CLI/API compatibility and future data variation
    test_share = 0.2
    test_count = max(1, int(total_files * test_share))
    source_count = max(1, total_files - test_count)

    _write(
        out / "pyproject.toml",
        '[project]\nname = "synthetic-repo"\nversion = "0.0.0"\ndependencies = ["requests==2.31.0"]\n',
    )
    _write(out / "README.md", "# Synthetic Repo\n\nGenerated for Sentinel scale benchmarks.\n")
    _write(out / "LICENSE", "MIT License\n")
    _write(out / ".gitignore", "__pycache__/\n*.pyc\n.sentinel/\n")
    _write(out / ".github" / "workflows" / "ci.yml", "name: ci\non: [push]\n")
    _write(out / "poetry.lock", "")

    for index in range(source_count):
        package = f"pkg_{index % 20}"
        module = f"{package}/module_{index:05d}.py"
        previous = ""
        if index > 0:
            previous = f"from pkg_{(index - 1) % 20}.module_{index - 1:05d} import value_{index - 1}\n"
        body = [
            '"""Synthetic module."""',
            "from __future__ import annotations",
            previous.rstrip(),
            "",
            f"VALUE_{index} = {index}",
            "",
            f"def value_{index}(left: int = {index}, right: int = 1) -> int:",
            f"    total = left + right + VALUE_{index}",
            "    if total > 10:",
            "        total -= 3",
            "    for step in range(2):",
            "        total += step",
            "    return total",
            "",
            f"class Service{index}:",
            "    def run(self, value: int) -> int:",
            f"        return value_{index}(value, {index % 7})",
            "",
        ]
        _write(out / module, "\n".join(body))

    for index in range(test_count):
        source_index = index % source_count
        package = f"pkg_{source_index % 20}"
        body = (
            f"from {package}.module_{source_index:05d} import value_{source_index}\n\n\n"
            f"def test_value_{index}() -> None:\n"
            f"    assert value_{source_index}() is not None\n"
        )
        _write(out / "tests" / f"test_module_{index:05d}.py", body)

    _write(out / "pkg_0" / "__init__.py", '"""Synthetic package."""\n')
    return source_count + test_count + 7


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, required=True, help="Approximate total source/test files")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--force", action="store_true", help="Replace a non-empty output directory")
    args = parser.parse_args(argv)
    if args.files < 10:
        raise SystemExit("--files must be at least 10")
    count = generate(Path(args.out).expanduser().resolve(), args.files, args.seed, args.force)
    print(f"generated {count} files in {Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
