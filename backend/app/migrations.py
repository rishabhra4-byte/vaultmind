"""Run database migrations at startup (Alembic), including for databases made by 1.0."""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from .database import engine

log = logging.getLogger(__name__)
ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"
BASELINE = "0001"


def alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_DIR.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    return cfg


def upgrade_database() -> None:
    cfg = alembic_config()
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        tables = set(inspect(connection).get_table_names())
        if "users" in tables and "alembic_version" not in tables:
            # Created by VaultMind 1.0 (create_all, no migration history): adopt it at the baseline.
            log.info("Existing database without migration history; marking it as revision %s", BASELINE)
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "head")
