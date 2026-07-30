from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / ".github" / "release_preflight.py"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


@pytest.fixture
def release_tree(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "domain-scout-ct"\nversion = "0.12.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "domain-scout-ct"\n'
        'version = "0.12.0"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n"
        "## [0.12.0] - 2026-07-30\n\n### Added\n\n- A release.\n\n"
        "## [0.11.0] - 2026-04-01\n\n- Earlier.\n",
        encoding="utf-8",
    )
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "release")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def _run(root: Path, tag: str = "v0.12.0") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(root),
            "--tag",
            tag,
            "--main-ref",
            "origin/main",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_release_preflight_accepts_consistent_main_release(release_tree: Path) -> None:
    result = _run(release_tree)
    assert result.returncode == 0
    assert "release preflight passed for domain-scout-ct 0.12.0" in result.stdout


def test_release_preflight_rejects_tag_version_mismatch(release_tree: Path) -> None:
    result = _run(release_tree, tag="v0.11.0")
    assert result.returncode == 1
    assert "does not match project version" in result.stderr


def test_release_preflight_rejects_lock_version_mismatch(release_tree: Path) -> None:
    (release_tree / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "domain-scout-ct"\n'
        'version = "0.11.0"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )
    result = _run(release_tree)
    assert result.returncode == 1
    assert "uv.lock project version" in result.stderr


def test_release_preflight_uses_project_name_from_manifest(release_tree: Path) -> None:
    (release_tree / "pyproject.toml").write_text(
        '[project]\nname = "renamed-package"\nversion = "0.12.0"\n',
        encoding="utf-8",
    )
    (release_tree / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "renamed-package"\n'
        'version = "0.12.0"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )
    result = _run(release_tree)
    assert result.returncode == 0
    assert "release preflight passed for renamed-package 0.12.0" in result.stdout


@pytest.mark.parametrize(
    "release_section",
    [
        "",
        "## [0.12.0] - 2026-07-30\n\n### Added\n\n",
        "## [0.12.0]\n\n- Missing date.\n",
    ],
)
def test_release_preflight_rejects_missing_or_empty_notes(
    release_tree: Path, release_section: str
) -> None:
    (release_tree / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [Unreleased]\n\n{release_section}"
        "## [0.11.0] - 2026-04-01\n\n- Earlier.\n",
        encoding="utf-8",
    )
    result = _run(release_tree)
    assert result.returncode == 1
    assert "CHANGELOG.md" in result.stderr


def test_release_preflight_rejects_commit_outside_main(release_tree: Path) -> None:
    (release_tree / "branch-only.txt").write_text("not on main\n", encoding="utf-8")
    _git(release_tree, "add", "branch-only.txt")
    _git(release_tree, "commit", "-m", "branch only")

    result = _run(release_tree)
    assert result.returncode == 1
    assert "not contained in origin/main" in result.stderr
