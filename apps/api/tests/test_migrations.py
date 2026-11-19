from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_API_ROOT = Path(__file__).resolve().parents[1]


def test_migrations_form_a_single_chain() -> None:
    # Two branches that both extend the same revision make
    # `alembic upgrade head` fail ("multiple heads") on the next deploy --
    # and the Dockerfile runs it on every start. CLAUDE.md says to check
    # before a PR; this makes CI check it.
    config = Config(str(_API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_API_ROOT / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"parallel migration heads: {heads}"
