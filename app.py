import os
import secrets
from datetime import timedelta

from flask import Flask, g
from flask_login import LoginManager
from werkzeug.middleware.proxy_fix import ProxyFix

from config.settings import ADMIN_USERNAME, SECRET_KEY, SESSION_TIMEOUT_MINUTES
from models import AdminUser


def create_app():
    # Validate SECRET_KEY before anything else
    if not SECRET_KEY or SECRET_KEY == "change-me-in-production":
        raise RuntimeError(
            "SECRET_KEY belum dikonfigurasi! Set environment variable SECRET_KEY "
            "dengan nilai random yang aman sebelum menjalankan aplikasi. "
            "Contoh: export SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')"
        )
    app = Flask(__name__)
    app.secret_key = SECRET_KEY

    # Railway (and any PaaS) terminate TLS at the edge and forward over HTTP,
    # so Flask sees scheme=http and a proxy IP. Without this, request.is_secure
    # is False and SESSION_COOKIE_SECURE cookies look wrong to the client.
    # x_for=1 x_proto=1 x_host=1 matches a single trusted edge proxy; do NOT
    # raise these counts unless you add matching trusted-proxy config.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.permanent_session_lifetime = timedelta(minutes=SESSION_TIMEOUT_MINUTES)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = True  # HTTPS only in production

    # CSRF handled manually via services/security.py
    app.config["WTF_CSRF_ENABLED"] = False

    # Security headers on every response
    from services.security import add_security_headers, generate_csrf_token
    app.after_request(add_security_headers)

    # Inject CSRF token + per-request CSP nonce into all templates
    @app.context_processor
    def inject_globals():
        def csp_nonce():
            return g.setdefault("csp_nonce", secrets.token_urlsafe(16))
        return dict(csrf_token=generate_csrf_token, csp_nonce=csp_nonce)

    # Login manager
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "admin.login"
    login_manager.login_message = "Silakan login terlebih dahulu."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(username):
        if username == ADMIN_USERNAME:
            return AdminUser(username)
        return None

    # Register blueprints — import here to avoid circular import
    from routes.admin import admin_bp
    from routes.public import public_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # Error handlers
    @app.errorhandler(404)
    def not_found(e):
        return "Halaman tidak ditemukan", 404

    @app.errorhandler(500)
    def server_error(e):
        return "Terjadi kesalahan server", 500

    @app.errorhandler(403)
    def forbidden(e):
        return f"Akses ditolak: {e.description}", 403

    @app.errorhandler(429)
    def too_many_requests(e):
        return f"Terlalu banyak permintaan: {e.description}", 429

    # Pre-warm Google Sheets connection + data cache at startup
    with app.app_context():
        try:
            from config.settings import SHEET_CUTI, SHEET_KARYAWAN
            from services.sheets_service import get_all_records
            get_all_records(SHEET_KARYAWAN)
            get_all_records(SHEET_CUTI)
            print("Sheets cache warmed")
        except Exception as e:
            print(f"Sheets warm-up failed (will retry on first request): {e}")

    return app


app = create_app()

if __name__ == "__main__":
    # In development, allow HTTP (disable Secure cookie flag)
    app.config["SESSION_COOKIE_SECURE"] = False
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5000))
    app.run(host=host, port=port, debug=os.environ.get('FLASK_DEBUG', '0') == '1')

