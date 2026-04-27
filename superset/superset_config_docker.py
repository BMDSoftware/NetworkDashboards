from datetime import timedelta
import os
import ast

FEATURE_FLAGS = {
    "DASHBOARD_NATIVE_FILTERS": False,
    "DASHBOARD_RBAC": True,
    "GENERIC_CHART_AXES": True,
    "ENABLE_TEMPLATE_PROCESSING": True,
    "ENABLE_TEMPLATE_REMOVE_FILTERS": True,
    "FLATTEN_INDEX_ON_CSV_EXPORT": True,
    "EMBEDDED_SUPERSET": os.environ.get("FEATURE_EMBEDDED_SUPERSET", "True") == "True"
}

# Guest token settings
GUEST_ROLE_NAME = os.environ.get("GUEST_ROLE_NAME")
GUEST_TOKEN_JWT_SECRET = os.environ.get("GUEST_TOKEN_JWT_SECRET")
GUEST_TOKEN_JWT_AUDIENCE = os.environ.get("GUEST_TOKEN_JWT_AUDIENCE")
GUEST_TOKEN_JWT_EXP_SECONDS = int(os.environ.get("GUEST_TOKEN_JWT_EXP_SECONDS"))

# Talisman / CORS
TALISMAN_ENABLED = os.environ.get("TALISMAN_ENABLED", "True") == "True"
ENABLE_CORS = os.environ.get("ENABLE_CORS", "True") == "True"
CORS_OPTIONS = {
    "origins": ast.literal_eval(f"[{os.environ.get('CORS_ORIGINS', '')}]"),
    "supports_credentials": True
}

TALISMAN_CONFIG = {
    "content_security_policy": {
        "frame-ancestors": ast.literal_eval(os.environ.get("CSP_FRAME_ANCESTORS", "['self']"))
    },
    "force_https": os.environ.get("TALISMAN_FORCE_HTTPS", "False") == "True",
    "session_cookie_secure": os.environ.get("TALISMAN_SESSION_COOKIE_SECURE", "True") == "True",
}

# CSRF headers
WTF_CSRF_HEADERS = os.environ.get("WTF_CSRF_HEADERS", "X-CSRFToken").split(",")

CHARTS_FOR_PIVOTED_CSV_EXPORT = []
if os.environ.get("CHARTS_FOR_PIVOTED_CSV_EXPORT", None) is not None:
    CHARTS_FOR_PIVOTED_CSV_EXPORT = os.environ["CHARTS_FOR_PIVOTED_CSV_EXPORT"].split(",")

SECRET_KEY = os.environ["SECRET_KEY"]