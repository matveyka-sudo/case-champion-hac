from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[3]


def migrate(url, schema, revision="head", downgrade=False):
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    config.attributes["database_url"] = url
    config.attributes["database_schema"] = schema
    (command.downgrade if downgrade else command.upgrade)(config, revision)
