"""
Security utilities: CSRF, rate limiting, security headers, safe error handling.
"""
import hmac
import secrets
import threading
import time
from functools import wraps

from flask import abort, g, request, session

from config.settings import TRUSTED_PROXIES

# ============================================================
# CSRF Protection
# ============================================================

def generate_csrf_token():
    """Generate or return existing CSRF token for the current session."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


def validate_csrf():
    """Validate CSRF token on POST requests. Call before processing."""
    if request.method == "POST":
        token = request.form.get("_csrf_token", "")
        expected = session.get("_csrf_token", "")
        if not token or not expected or not hmac.compare_digest(token, expected):
            abort(403, description="CSRF token tidak valid. Silakan refresh halaman.")


# ============================================================
# Rate Limiting (in-memory, per-IP)
# ============================================================

_rate_limits = {}  # ip -> {\"endpoint\": [(timestamp, ...)]}
_rate_limit_lock = threading.Lock()
_rate_limit_cleanup_counter = 0
RATE_LIMIT_CLEANUP_INTERVAL = 100  # run global cleanup every N requests


def _cleanup_stale_keys():
    """Remove keys where all timestamps have expired. Thread-safe."""
    now = time.time()
    # Use a snapshot of keys to avoid RuntimeError during iteration
    with _rate_limit_lock:
        keys_to_check = list(_rate_limits.keys())
    stale_keys = []
    for key in keys_to_check:
        with _rate_limit_lock:
            timestamps = _rate_limits.get(key, [])
            if not timestamps:
                stale_keys.append(key)
            else:
                # Check if ALL timestamps are expired (max timestamp is old)
                if max(timestamps) < now - 7200:  # 2 hour max window
                    stale_keys.append(key)
    if stale_keys:
        with _rate_limit_lock:
            for key in stale_keys:
                _rate_limits.pop(key, None)


def rate_limit(max_requests, window_seconds, key_func=None, methods=None):
    """
    Decorator: limit requests per IP per time window.
    key_func(request) -> str for custom key (default: remote_addr).
    methods: list of HTTP methods to rate-limit (default: all). E.g. ["POST"].
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # Skip rate limiting if method not in target methods
            if methods and request.method not in methods:
                return f(*args, **kwargs)

            global _rate_limit_cleanup_counter
            _rate_limit_cleanup_counter += 1

            # Periodic global cleanup of stale keys
            if _rate_limit_cleanup_counter % RATE_LIMIT_CLEANUP_INTERVAL == 0:
                _cleanup_stale_keys()

            ip = get_real_ip()
            key = key_func(request) if key_func else ip
            cache_key = f"{f.__name__}:{key}"
            now = time.time()
            cutoff = now - window_seconds

            with _rate_limit_lock:
                if cache_key not in _rate_limits:
                    _rate_limits[cache_key] = []

                # Prune old entries
                _rate_limits[cache_key] = [
                    t for t in _rate_limits[cache_key] if t > cutoff
                ]

                # Remove key entirely if empty after pruning
                if not _rate_limits[cache_key]:
                    del _rate_limits[cache_key]
                    _rate_limits[cache_key] = []

                if len(_rate_limits[cache_key]) >= max_requests:
                    abort(429, description="Terlalu banyak percobaan. Coba lagi nanti.")

                _rate_limits[cache_key].append(now)

            return f(*args, **kwargs)
        return wrapper
    return decorator


# ============================================================
# IP Address (handle reverse proxy)
# ============================================================

def get_real_ip():
    """Get real client IP.

    Strategy:
    1. If the direct peer (REMOTE_ADDR) is a configured trusted proxy,
       trust X-Forwarded-For (explicit TRUSTED_PROXIES config).
    2. If REMOTE_ADDR is a private/internal address AND X-Forwarded-For is
       present, treat the connection as behind a PaaS ingress (Railway,
       Heroku, Render) and trust XFF.
    3. Otherwise fall back to REMOTE_ADDR (immune to XFF spoofing).

    XFF parsing: take the LAST value (added by our trusted proxy), not the
    first (which may be spoofed by the client). Proxies append, so the
    rightmost entry is the one our proxy added.
    """
    remote = request.remote_addr or "unknown"

    if TRUSTED_PROXIES and remote in TRUSTED_PROXIES and request.headers.get("X-Forwarded-For"):
        return request.headers["X-Forwarded-For"].split(",")[-1].strip()

    xff = request.headers.get("X-Forwarded-For")
    if xff and _is_private_ip(remote):
        return xff.split(",")[-1].strip()

    return remote


def _is_private_ip(ip: str) -> bool:
    """True if ip is a private/internal (RFC1918) or loopback address."""
    try:
        import ipaddress
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback
    except ValueError:
        return False


# ============================================================
# Security Headers Middleware
# ============================================================

def add_security_headers(response):
    """Add security headers to every response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # HSTS: only meaningful over HTTPS. Behind Railway/proxies the request can
    # still look like http to Flask unless ProxyFix is applied in app.py.
    # ProxyFix consumes X-Forwarded-Proto, so request.is_secure is the
    # authoritative signal here once that middleware is in place.
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Nonce dibuat saat render template (lihat inject_globals di app.py);
    # untuk respons tanpa render (redirect/file) generate nonce sisa.
    nonce = g.get("csp_nonce") or secrets.token_urlsafe(16)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    return response


# ============================================================
# Safe Error Messages
# ============================================================

def safe_error_message(e, context="operasi"):
    """Return generic error message, log the real error server-side."""
    import logging
    logging.error(f"Error during {context}: {e}", exc_info=True)
    return f"Terjadi kesalahan saat {context}. Silakan coba lagi atau hubungi admin."
