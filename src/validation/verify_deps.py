"""Runtime dependency version verification.

Every training process, Optuna worker, or CI run must call
``verify_runtime_dependencies()`` at startup to confirm the installed
dependency versions match expectations.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

REQUIRED: dict[str, str] = {
    "xgboost": "3.2.0",
    "purgedcv": "0.1.2",
}


def verify_runtime_dependencies() -> None:
    """Raise ``RuntimeError`` if any required package is missing or wrong.

    Example::

        from src.validation.verify_deps import verify_runtime_dependencies
        verify_runtime_dependencies()  # fails fast at startup
    """
    problems: list[str] = []

    for package, expected in REQUIRED.items():
        try:
            installed = version(package)
        except PackageNotFoundError:
            problems.append(f"{package}: missing")
            continue

        if installed != expected:
            problems.append(
                f"{package}: expected {expected}, found {installed}"
            )

    if problems:
        raise RuntimeError(
            "Runtime dependency validation failed:\n"
            + "\n".join(problems)
        )
