from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


class UnsafeValueError(ValueError):
    pass


def _finalize(value: Any) -> Any:
    # Every value substituted into a config file must stay on its own line.
    # Validation should already have caught this; this is the last line of
    # defense against config injection.
    if isinstance(value, str) and ("\n" in value or "\r" in value or "\x00" in value):
        raise UnsafeValueError(f"refusing to render multi-line value {value[:30]!r}")
    return value


def make_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        undefined=StrictUndefined,
        autoescape=False,  # noqa: S701 - output is config files, not HTML
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
        finalize=_finalize,
    )


env = make_env()
