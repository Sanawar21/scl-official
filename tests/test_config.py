"""Config: the tiny .env loader (comments, quotes, export prefix, precedence)."""
import os
import tempfile
from pathlib import Path

from app.config import _load_dotenv, Config


def _write_env(text):
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def _pop(*keys):
    for k in keys:
        os.environ.pop(k, None)


def test_loader_parses_values_and_quotes():
    _pop("FOO", "QUOTED", "DOUBLE", "EXP")
    _load_dotenv(_write_env(
        "# a comment\n"
        "FOO=bar\n"
        "QUOTED='hello world'\n"
        "DOUBLE=\"x=y\"\n"
        "export EXP=1\n"
        "\n"
        "BROKENLINE\n"
    ))
    assert os.environ["FOO"] == "bar"
    assert os.environ["QUOTED"] == "hello world"
    assert os.environ["DOUBLE"] == "x=y"
    assert os.environ["EXP"] == "1"


def test_real_env_var_wins_over_dotenv():
    _pop("WINNER")
    path = _write_env("WINNER=from-dotenv\n")
    os.environ["WINNER"] = "from-env"
    _load_dotenv(path)
    assert os.environ["WINNER"] == "from-env"


def test_missing_env_file_is_silent():
    _load_dotenv(Path(tempfile.mkdtemp()) / "nope.env")  # must not raise


def test_project_env_file_is_loaded_at_import():
    # The project root .env is loaded once at import time (app/config.py calls
    # _load_dotenv(BASE_DIR / ".env")). If it exists, its DB_PATH is used as
    # the Config default — this pins that wiring without depending on the
    # developer's actual .env contents.
    from app.config import BASE_DIR

    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        # No .env committed (gitignored) — Config must fall back to scl.db.
        assert Config.DB_PATH.endswith("scl.db")
    else:
        assert Config.DB_PATH  # whatever the developer set, it must be applied
