"""Cross-cutting browser security for SL-Office."""

from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()

_CSP = (
    "default-src 'self'; base-uri 'self'; frame-ancestors {frames}; form-action 'self'; "
    "object-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; font-src 'self'"
)


def allow_same_origin_framing(response):
    """Die Antwort darf in einem Rahmen von SL-Office selbst erscheinen.

    Die Dokumentenansicht der Schülerakte zeigt hochgeladene PDFs in einem
    iframe; fremde Seiten dürfen sie weiterhin nicht einbetten.
    """
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = _CSP.format(frames="'self'")
    return response


def init_security(app):
    csrf.init_app(app)

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Content-Security-Policy", _CSP.format(frames="'none'"))
        response.headers.setdefault("Cache-Control", "no-store")
        if app.config.get("ENV_NAME") == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
