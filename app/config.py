import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (no dependency): KEY=value lines, '#' comments, quotes.

    Real environment variables always win — a .env value only fills in when the
    variable isn't already set (same behavior as python-dotenv's default). If a
    line is missing or the file doesn't exist, it's a silent no-op.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.environ.get("SCL_SECRET_KEY", "scl-dev-secret")
    DB_PATH = os.environ.get("SCL_DB_PATH", str(BASE_DIR / "data" / "scl.db"))
    ADMIN_USERNAME = os.environ.get("SCL_ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("SCL_ADMIN_PASSWORD", "admin123")
    SESSION_COOKIE_HTTPONLY = True
