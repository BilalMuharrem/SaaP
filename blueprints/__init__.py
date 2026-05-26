"""
blueprints/ — Flask blueprint modülleri.

Her modül kendi `bp` örneğini ve route'larını tanımlar.
create_app() içinden register_blueprints(app) ile kayıt edilir.
"""
from .auth import bp as auth_bp
from .dashboard import bp as dashboard_bp
from .jobs import bp as jobs_bp
from .tracked import bp as tracked_bp
from .seo import bp as seo_bp
from .ai_consultant import bp as ai_consultant_bp
from .notifications import bp as notifications_bp
from .plans import bp as plans_bp
from .admin import bp as admin_bp
from .onboarding import bp as onboarding_bp
from .health import bp as health_bp
from .demo import bp as demo_bp


def register_blueprints(app):
    """Tüm blueprint'leri Flask app'e kayıt eder."""
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(tracked_bp)
    app.register_blueprint(seo_bp)
    app.register_blueprint(ai_consultant_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(plans_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(onboarding_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(demo_bp)
