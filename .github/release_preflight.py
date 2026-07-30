#!/usr/bin/env python3
"""Fail-closed validation for tag-triggered releases."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import NoReturn

PROJECT_NAME = "domain-scout-ct"


def _fail(message: str) -> NoReturn:
    raise ValueError(message)


def _project_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle).get("project", {})
    version = project.get("version")
    if not isinstance(version, str) or not version:
        _fail("pyproject.toml has no non-empty project.version")
    return version


def _locked_project_version(root: Path) -> str:
    with (root / "uv.lock").open("rb") as handle:
        packages = tomllib.load(handle).get("package", [])
    matches = [
        package
        for package in packages
        if package.get("name") == PROJECT_NAME and package.get("source", {}).get("editable") == "."
    ]
    if len(matches) != 1:
        _fail(f"uv.lock must contain exactly one editable {PROJECT_NAME!r} package")
    version = matches[0].get("version")
    if not isinstance(version, str) or not version:
        _fail(f"uv.lock has no version for editable {PROJECT_NAME!r}")
    return version


def _validate_changelog(root: Path, version: str) -> None:
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.compile(
        rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}\s*$",
        re.MULTILINE,
    )
    match = heading.search(changelog)
    if match is None:
        _fail(f"CHANGELOG.md has no dated [{version}] release section")
    next_heading = re.search(r"^## \[", changelog[match.end() :], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(changelog)
    release_notes = changelog[match.end() : end]
    if re.search(r"^- ", release_notes, re.MULTILINE) is None:
        _fail(f"CHANGELOG.md [{version}] release section has no entries")


def _validate_main_ancestry(root: Path, main_ref: str) -> None:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "HEAD", main_ref],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    if result.returncode == 1:
        _fail(f"release commit is not contained in {main_ref}")
    detail = result.stderr.strip() or "git merge-base failed"
    _fail(f"could not verify release ancestry against {main_ref}: {detail}")


def validate_release(root: Path, tag: str, main_ref: str) -> str:
    version = _project_version(root)
    expected_tag = f"v{version}"
    if tag != expected_tag:
        _fail(f"release tag {tag!r} does not match project version {expected_tag!r}")

    locked_version = _locked_project_version(root)
    if locked_version != version:
        _fail(
            f"uv.lock project version {locked_version!r} does not match pyproject.toml {version!r}"
        )

    _validate_changelog(root, version)
    _validate_main_ancestry(root, main_ref)
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    try:
        version = validate_release(args.root.resolve(), args.tag, args.main_ref)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as error:
        print(f"release preflight failed: {error}", file=sys.stderr)
        return 1

    print(f"release preflight passed for {PROJECT_NAME} {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
