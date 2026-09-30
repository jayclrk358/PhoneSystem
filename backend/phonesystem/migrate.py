"""Run database migrations from code (used by the CLI, the server and tests)."""

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(db_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def upgrade(db_url: str) -> None:
    command.upgrade(alembic_config(db_url), "head")
