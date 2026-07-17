from __future__ import annotations

import asyncio
import sys
from typing import Any, Callable, Coroutine

from scripts.regression import (
    check_crud,
    check_database,
    check_isbn,
    check_makefile,
    check_price,
    check_routes,
    check_seed_cli,
    check_templates,
    check_validation,
)
from scripts.regression.assertions import (
    StopRegressionChecks,
    run_async_check,
    run_check,
)
from scripts.regression.context import RegressionContext, check_python_compile


def _check_route_and_model_contracts(context: RegressionContext) -> None:
    check_routes.check_contracts(context)
    check_database.check_model_contracts(context)


def _check_seed_cli_and_makefile(context: RegressionContext) -> None:
    check_seed_cli.check_seed_cli(context)
    check_makefile.check_makefile(context)


def _run_async(
    name: str,
    operation: Callable[[], Coroutine[Any, Any, Any]],
) -> None:
    asyncio.run(run_async_check(name, operation))


def main() -> int:
    context = RegressionContext()
    regression_failed = False

    try:
        run_check("Python compile", check_python_compile)

        with context:
            run_check("application import", context.import_application)
            run_check("isolated database setup", context.initialize_database)
            run_check(
                "route and single-model contracts",
                lambda: _check_route_and_model_contracts(context),
            )
            run_check(
                "ISBN policy and conversion",
                lambda: check_isbn.check_policy(context),
            )
            run_check(
                "price policy and cents conversion",
                lambda: check_price.check_policy(context),
            )
            run_check(
                "price display formatting",
                lambda: check_price.check_display(context),
            )
            run_check(
                "url_for source structure",
                check_templates.check_url_for_source,
            )
            run_check(
                "single-model source structure",
                check_database.check_single_model_source,
            )
            run_check(
                "price cents source structure",
                check_price.check_source_structure,
            )
            run_check(
                "seed CLI and Makefile",
                lambda: _check_seed_cli_and_makefile(context),
            )
            _run_async(
                "basic pages and template URLs",
                lambda: check_routes.check_basic_pages(context),
            )
            _run_async(
                "create and search flow",
                lambda: check_crud.check_create_and_search(context),
            )
            _run_async(
                "update flow",
                lambda: check_crud.check_update(context),
            )
            _run_async(
                "price cents Create and Update flow",
                lambda: check_price.check_create_update_and_validation(
                    context
                ),
            )
            _run_async(
                "ISBN canonicalization and duplicate handling",
                lambda: check_isbn.check_canonicalization_and_duplicates(
                    context
                ),
            )
            _run_async(
                "validation and original input preservation",
                lambda: check_validation.check_form_validation(context),
            )
            _run_async(
                "error pages and delete flow",
                lambda: check_routes.check_error_pages_and_delete(context),
            )
            run_check(
                "sqlite single table and constraints",
                lambda: check_database.check_database_behavior(context),
            )
    except StopRegressionChecks:
        regression_failed = True
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        print(f"[FAIL] regression setup: {detail}", file=sys.stderr)
        regression_failed = True

    if context.library_is_unchanged():
        print("[PASS] library.db unchanged")
    else:
        print("[FAIL] library.db unchanged", file=sys.stderr)
        regression_failed = True

    if context.temporary_artifacts_are_cleaned():
        print("[PASS] temporary artifacts cleaned")
    else:
        print(
            "[FAIL] temporary artifacts cleaned: "
            f"{context.temporary_root} remains",
            file=sys.stderr,
        )
        regression_failed = True

    if regression_failed:
        print("Regression checks failed.", file=sys.stderr)
        return 1

    print("All regression checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
