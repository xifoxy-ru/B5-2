from __future__ import annotations

import re
import subprocess
import tomllib

from scripts.regression.assertions import require
from scripts.regression.context import MAKEFILE_PATH, PROJECT_ROOT, RegressionContext


PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
UV_LOCK_PATH = PROJECT_ROOT / "uv.lock"
PYTHON_VERSION_PATH = PROJECT_ROOT / ".python-version"
REQUIREMENTS_PATH = PROJECT_ROOT / "requirements.txt"


def check_makefile(context: RegressionContext) -> None:
    temporary_root = context.temporary_root
    require(temporary_root is not None, "temporary root is unavailable")
    require(MAKEFILE_PATH.is_file(), "Makefile is missing")
    require(PYPROJECT_PATH.is_file(), "pyproject.toml is missing")
    require(UV_LOCK_PATH.is_file(), "uv.lock is missing")
    require(PYTHON_VERSION_PATH.is_file(), ".python-version is missing")
    require(not REQUIREMENTS_PATH.exists(), "requirements.txt still exists")

    pyproject = tomllib.loads(PYPROJECT_PATH.read_text())
    require(
        pyproject["project"]["requires-python"] == ">=3.12,<3.13",
        "pyproject Python range differs",
    )
    require(
        pyproject["project"]["dependencies"]
        == [
            "fastapi==0.139.0",
            "jinja2==3.1.6",
            "python-multipart==0.0.32",
            "sqlalchemy==2.0.51",
            "uvicorn==0.51.0",
        ],
        "pyproject direct dependencies differ",
    )
    require(
        pyproject["tool"]["uv"]["package"] is False,
        "uv package mode must be disabled",
    )
    require(
        PYTHON_VERSION_PATH.read_text().strip() == "3.12",
        ".python-version differs",
    )

    makefile_source = MAKEFILE_PATH.read_text()
    make_targets = re.findall(
        r"^([A-Za-z0-9_-]+):",
        makefile_source,
        re.MULTILINE,
    )
    expected_targets = [
        "help",
        "setup",
        "run",
        "test",
        "clean",
        "db-seed",
        "db-reset",
    ]
    require(
        make_targets == expected_targets,
        f"Make targets differ: {make_targets!r}",
    )
    phony_match = re.search(
        r"^\.PHONY:\s*(.+)$",
        makefile_source,
        re.MULTILINE,
    )
    require(phony_match is not None, "Makefile .PHONY declaration is missing")
    require(
        set(phony_match.group(1).split()) == set(expected_targets),
        "Makefile .PHONY targets differ",
    )
    require(
        "$(UV) python install 3.12" in makefile_source,
        "make setup Python install command differs",
    )
    require(
        "$(UV) sync --locked" in makefile_source,
        "make setup sync command differs",
    )
    require(
        "$(UV) run uvicorn app.main:app --reload" in makefile_source,
        "make run command differs",
    )
    require(
        "PYTHONDONTWRITEBYTECODE=1 "
        "$(UV) run python scripts/regression_check.py" in makefile_source,
        "make test command differs",
    )
    require(
        "$(UV) run python scripts/db_cli.py seed" in makefile_source,
        "make db-seed command differs",
    )
    require(
        "$(UV) run python scripts/db_cli.py reset" in makefile_source,
        "make db-reset command differs",
    )
    for forbidden_reference in (
        ".venv/bin/python",
        "pip install",
        "requirements.txt",
    ):
        require(
            forbidden_reference not in makefile_source,
            f"Makefile still references {forbidden_reference}",
        )
    for forbidden_target in (
        "db-init:",
        "db-mock:",
        "db-clear:",
        "db-execute:",
    ):
        require(
            forbidden_target not in makefile_source,
            f"Makefile exposes {forbidden_target[:-1]}",
        )

    make_help = subprocess.run(
        ["make", "-f", str(MAKEFILE_PATH), "help"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    require(make_help.returncode == 0, f"make help failed: {make_help.stderr}")
    for target in expected_targets:
        require(
            f"make {target}" in make_help.stdout,
            f"make help omits {target}",
        )

    clean_root = temporary_root / "make-clean-check"
    (clean_root / ".git").mkdir(parents=True)
    (clean_root / ".venv").mkdir()
    (clean_root / "nested" / "__pycache__").mkdir(parents=True)
    (clean_root / ".pytest_cache").mkdir()
    (clean_root / ".mypy_cache").mkdir()
    (clean_root / ".ruff_cache").mkdir()
    (clean_root / "__MACOSX").mkdir()
    (clean_root / ".git" / "protected.pyc").write_text("keep")
    (clean_root / ".venv" / "protected.pyc").write_text("keep")
    (clean_root / "nested" / "__pycache__" / "cache.pyc").write_text(
        "remove"
    )
    (clean_root / ".pytest_cache" / "state").write_text("remove")
    (clean_root / ".mypy_cache" / "state").write_text("remove")
    (clean_root / ".ruff_cache" / "state").write_text("remove")
    (clean_root / "__MACOSX" / "state").write_text("remove")
    (clean_root / ".DS_Store").write_text("remove")
    (clean_root / "._metadata").write_text("remove")

    make_clean = subprocess.run(
        ["make", "-f", str(MAKEFILE_PATH), "clean"],
        cwd=clean_root,
        check=False,
        capture_output=True,
        text=True,
    )
    require(
        make_clean.returncode == 0,
        f"make clean failed: {make_clean.stderr}",
    )
    require(
        (clean_root / ".git" / "protected.pyc").exists(),
        "make clean touched .git",
    )
    require(
        (clean_root / ".venv" / "protected.pyc").exists(),
        "make clean touched .venv",
    )
    for removed_path in (
        clean_root / "nested" / "__pycache__",
        clean_root / ".pytest_cache",
        clean_root / ".mypy_cache",
        clean_root / ".ruff_cache",
        clean_root / "__MACOSX",
        clean_root / ".DS_Store",
        clean_root / "._metadata",
    ):
        require(
            not removed_path.exists(),
            f"make clean left {removed_path.name}",
        )
