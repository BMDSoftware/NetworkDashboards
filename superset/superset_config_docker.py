from datetime import timedelta
import os

FEATURE_FLAGS = {
    "DASHBOARD_NATIVE_FILTERS": False,
    "DASHBOARD_RBAC": True,
    "GENERIC_CHART_AXES": True,
    "ENABLE_TEMPLATE_PROCESSING": True,
    "ENABLE_TEMPLATE_REMOVE_FILTERS": True,
    "FLATTEN_INDEX_ON_CSV_EXPORT": True
}

CHARTS_FOR_PIVOTED_CSV_EXPORT = []

SECRET_KEY = os.environ["SECRET_KEY"]

CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
}

FILTER_STATE_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": int(timedelta(days=90).total_seconds()),
    # should the timeout be reset when retrieving a cached value
    "REFRESH_TIMEOUT_ON_RETRIEVAL": True,
}

EXPLORE_FORM_DATA_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": int(timedelta(days=7).total_seconds()),
    # should the timeout be reset when retrieving a cached value
    "REFRESH_TIMEOUT_ON_RETRIEVAL": True,
}
