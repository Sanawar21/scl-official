from flask import Flask, current_app, session, url_for
from flask_socketio import SocketIO

from .authz import current_user
from .config import Config
from .db import Database
from .services.auction_service import AuctionService
from .services.auth_service import AuthService
from .services.bank_service import BankService
from .services.branding_service import BrandingService
from .services.changelog_service import ChangelogService
from .services.finance_service import FinanceService
from .services.auction_pdf_service import AuctionPdfService
from .services.scorer_service import ScorerService
from .services.scorecard_service import ScorecardService
from .services.scenario_service import ScenarioService
from .services.wager_service import WagerService

socketio = SocketIO(async_mode="threading")


def create_app(config_object=None):
    app = Flask(__name__)
    if config_object is None:
        app.config.from_object(Config)
    elif isinstance(config_object, dict):
        app.config.from_object(Config)
        app.config.update(config_object)
    else:
        app.config.from_object(config_object)

    db = Database(app.config["DB_PATH"])
    db.bootstrap()

    auth_service = AuthService(db)
    bank_service = BankService(db)
    auction_service = AuctionService(db, bank_service)
    wager_service = WagerService(db, bank_service)
    scorer_service = ScorerService(db)
    finance_service = FinanceService(db, bank_service, auction_service)
    scorecard_service = ScorecardService()
    auction_pdf_service = AuctionPdfService()
    scenario_service = ScenarioService(db, scorer_service)
    changelog_service = ChangelogService(db)
    branding_service = BrandingService()

    auth_service.seed_admin_if_missing(app.config.get("ADMIN_USERNAME"),
                                       app.config.get("ADMIN_PASSWORD"))

    app.extensions["db"] = db
    app.extensions["auth_service"] = auth_service
    app.extensions["auction_service"] = auction_service
    app.extensions["bank_service"] = bank_service
    app.extensions["wager_service"] = wager_service
    app.extensions["scorer_service"] = scorer_service
    app.extensions["finance_service"] = finance_service
    app.extensions["scorecard_service"] = scorecard_service
    app.extensions["auction_pdf_service"] = auction_pdf_service
    app.extensions["scenario_service"] = scenario_service
    app.extensions["changelog_service"] = changelog_service
    app.extensions["branding_service"] = branding_service

    from .routes.admin import admin_bp
    from .routes.auth import auth_bp
    from .routes.banking import banking_bp
    from .routes.manager import manager_bp
    from .routes.matches import matches_bp
    from .routes.viewer import viewer_bp
    from .routes.wagers import wagers_bp

    app.register_blueprint(admin_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(banking_bp)
    app.register_blueprint(manager_bp)
    app.register_blueprint(matches_bp)
    app.register_blueprint(viewer_bp)
    app.register_blueprint(wagers_bp)

    @app.context_processor
    def inject_globals():
        return {"current_user": current_user(), "nav_auction": _nav_auction(current_user())}

    socketio.init_app(app)
    return app


def _nav_auction(user):
    """Nav "Live Auction" chip state for the current user, or None for anon.

    Live = the user's (manager) or any (admin) season is in an auction phase
    (setup/complete/transfers excluded). Cheap lookups only — no full state."""
    if not user:
        return None
    auction = current_app.extensions["auction_service"]

    def _is_live(ph):
        return ph and ph not in ("setup", "complete", "transfers_open")

    if user.get("role") == "admin":
        for s in auction.list_seasons() or []:
            try:
                if _is_live(auction.get_phase(s["id"])):
                    return {"live": True, "url": url_for("admin.auction", season=s["id"])}
            except Exception:
                continue
        return {"live": False, "url": None}
    if user.get("season_id"):
        try:
            ph = auction.get_phase(user["season_id"])
        except Exception:
            ph = None
        if _is_live(ph):
            return {"live": True, "url": url_for("manager.dashboard")}
    return {"live": False, "url": None}


def emit_state(season_id: str):
    """Broadcast a state refresh to live clients after any mutation."""
    socketio.emit("state_update", {"season_id": season_id})
