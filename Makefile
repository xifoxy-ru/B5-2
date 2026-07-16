PYTHON := .venv/bin/python

.PHONY: help run test clean db-seed db-reset

help:
	@printf '%-15s %s\n' \
		'make help' 'Show available commands' \
		'make run' 'Run the FastAPI development server' \
		'make test' 'Run the full regression check' \
		'make clean' 'Remove caches and OS metadata' \
		'make db-seed' 'Seed an empty library database' \
		'make db-reset' 'Recreate and seed the library database'

run:
	$(PYTHON) -m uvicorn app.main:app --reload

test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/regression_check.py

clean:
	find . \( -path './.git' -o -path './.venv' \) -prune -o -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.mypy_cache' -o -name '.ruff_cache' -o -name '__MACOSX' \) -prune -exec rm -rf {} +
	find . \( -path './.git' -o -path './.venv' \) -prune -o -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '.DS_Store' -o -name '._*' \) -exec rm -f {} +

db-seed:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/db_cli.py seed

db-reset:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/db_cli.py reset
