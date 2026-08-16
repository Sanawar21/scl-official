"""SCL + team branding: asset resolution with SCL fallback, and team uploads.

Brand assets live under ``data/brandings/``:

- ``scl/`` — the league's own graphics (logo marks, wide banner, full image),
  shipped with the repo and served read-only via ``/branding/scl/<file>``.
- ``teams/<team_id>/`` — files uploaded by managers/admins (logo + banner).

The DB stores a *relative key* for uploaded files (e.g. ``teams/<id>/logo.png``)
or a full external URL. ``resolve()`` turns any of those into a servable URL;
``team_logo()`` / ``team_banner()`` fall back to the SCL brand when the team
has no asset of their own, so the league identity is never lost.
"""

import re
from pathlib import Path

from flask import current_app, send_file

from ..config import BASE_DIR

BRANDING_ROOT = BASE_DIR / "data" / "brandings"
SCL_DIR = BRANDING_ROOT / "scl"
TEAMS_DIR = BRANDING_ROOT / "teams"

# Friendly names -> actual filenames inside data/brandings/scl/.
SCL_ASSETS = {
    "logo": "logo-only-light-bg-square.JPG",       # square mark on light bg
    "logo_dark": "logo-only-dark-bg-square.JPG",   # square mark on dark bg
    "mark": "logo-mark-16-9.JPG",                  # 16:9 mark
    "banner": "wide-banner.JPG",                   # wide hero banner
    "full": "full.jpg",                            # full brand image
}

ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_MAX_UPLOAD = 5 * 1024 * 1024  # 5 MB


class BrandingService:
    # ------------------------------------------------------------------
    # URL building
    # ------------------------------------------------------------------
    @staticmethod
    def scl_url(kind: str) -> str:
        """URL for a league asset, e.g. ``/branding/scl/wide-banner.JPG``."""
        filename = SCL_ASSETS.get(kind)
        if not filename:
            raise ValueError(f"Unknown SCL asset kind: {kind}")
        return f"/branding/scl/{filename}"

    @staticmethod
    def _resolve_value(value: str) -> str:
        """Turn a stored asset value (URL or relative key) into a URL."""
        value = (value or "").strip()
        if not value:
            return ""
        if re.match(r"^https?://", value, re.IGNORECASE):
            return value
        # Relative key like "teams/<id>/logo.png" -> serve from /branding/.
        value = value.lstrip("/")
        return f"/branding/{value}"

    @staticmethod
    def team_logo(team) -> str:
        """The team's logo URL, falling back to the SCL square mark."""
        value = (team or {}).get("logo") or ""
        return BrandingService._resolve_value(value) or BrandingService.scl_url("logo")

    @staticmethod
    def team_banner(team) -> str:
        """The team's banner URL, falling back to the SCL wide banner."""
        value = (team or {}).get("banner") or ""
        return BrandingService._resolve_value(value) or BrandingService.scl_url("banner")

    @staticmethod
    def team_assets(team) -> dict:
        """{logo, banner} URLs for a team (global_teams dict or row-like)."""
        return {
            "logo": BrandingService.team_logo(team),
            "banner": BrandingService.team_banner(team),
        }

    # ------------------------------------------------------------------
    # uploads / removal
    # ------------------------------------------------------------------
    def save_team_asset(self, team_id: str, kind: str, file_storage) -> str:
        """Persist an uploaded logo/banner for a team.

        Returns the relative key stored in the DB (e.g. ``teams/<id>/logo.png``).
        Raises ValueError on bad kind/extension/size.
        """
        if kind not in ("logo", "banner"):
            raise ValueError("Asset kind must be 'logo' or 'banner'")
        if not file_storage or not getattr(file_storage, "filename", ""):
            raise ValueError("No file uploaded")
        filename = (file_storage.filename or "").lower()
        ext = Path(filename).suffix
        if ext not in ALLOWED_EXTS:
            raise ValueError(f"Unsupported file type '{ext or 'none'}'. "
                             "Use JPG, PNG, WEBP or GIF.")
        file_storage.stream.seek(0, 2)
        size = file_storage.stream.tell()
        file_storage.stream.seek(0)
        if size > _MAX_UPLOAD:
            raise ValueError("Image is larger than 5 MB.")

        team_dir = TEAMS_DIR / team_id
        team_dir.mkdir(parents=True, exist_ok=True)
        target = team_dir / f"{kind}{ext}"
        file_storage.save(str(target))
        return f"teams/{team_id}/{kind}{ext}"

    def remove_team_asset(self, team_id: str, kind: str) -> str:
        """Delete the stored file for a team asset, returning the DB-cleared key."""
        if kind not in ("logo", "banner"):
            raise ValueError("Asset kind must be 'logo' or 'banner'")
        team_dir = TEAMS_DIR / team_id
        for candidate in team_dir.glob(f"{kind}.*"):
            try:
                candidate.unlink()
            except OSError:
                pass
        return ""

    # ------------------------------------------------------------------
    # serving
    # ------------------------------------------------------------------
    def serve(self, relpath: str):
        """Serve a file from BRANDING_ROOT (route helper). Path traversal safe."""
        rel = Path(relpath)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("Bad path")
        target = (BRANDING_ROOT / rel).resolve()
        if not str(target).startswith(str(BRANDING_ROOT.resolve())):
            raise ValueError("Bad path")
        if not target.is_file():
            return None
        return send_file(target, conditional=True)


def asset_kind_url(kind: str) -> str:
    return BrandingService.scl_url(kind)
