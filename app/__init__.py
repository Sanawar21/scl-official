from flask import Flask, session
from flask_socketio import SocketIO

from .config import Config
from .db import Database
from .services.auction_service import AuctionService
from .services.auth_service import AuthService
from .services.bank_service import BankService
from .services.finance_service import FinanceService
from .services.scorer_service import ScorerService
from .services.scorecard_service import ScorecardService
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
        return {"current_user": session.get("user")}

    socketio.init_app(app)
    return app


def emit_state(season_id: str):
    """Broadcast a state refresh to live clients after any mutation."""
    socketio.emit("state_update", {"season_id": season_id})
