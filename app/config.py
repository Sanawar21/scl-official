import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.environ.get("SCL_SECRET_KEY", "scl-dev-secret")
    DB_PATH = os.environ.get("SCL_DB_PATH", str(BASE_DIR / "data" / "scl.db"))
    ADMIN_USERNAME = os.environ.get("SCL_ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("SCL_ADMIN_PASSWORD", "admin123")
    SESSION_COOKIE_HTTPONLY = True
