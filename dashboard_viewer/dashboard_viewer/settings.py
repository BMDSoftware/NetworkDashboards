"""
Django settings for dashboard_viewer project.

Generated by 'django-admin startproject' using Django 2.2.5.

For more information on this file, see
https://docs.djangoproject.com/en/2.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/2.2/ref/settings/
"""
import logging
import os
from collections import OrderedDict
from distutils.util import strtobool

from constance.signals import config_updated
from django.core.validators import _lazy_re_compile, URLValidator
from django.dispatch import receiver
from sqlalchemy import create_engine

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/2.2/howto/deployment/checklist/

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get("DASHBOARD_VIEWER_ENV", "development") == "development"


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "custom": {
            "format": "%(asctime)s %(levelname)s %(name)s:%(lineno)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "custom",
        },
    },
    "loggers": {
        "root": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
        },
    },
}


_DEFAULT_SECRET_KEY = "CHANGE_ME"  # noqa

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get("SECRET_KEY", _DEFAULT_SECRET_KEY)
if not DEBUG and SECRET_KEY == _DEFAULT_SECRET_KEY:
    logging.getLogger(__name__).warning(
        "Using the default secret key. If this is a production environment please change it.",
    )


ALLOWED_HOSTS = ["*"]


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "bootstrap4",
    "bootstrap_datepicker_plus",
    "constance",
    "django_celery_results",
    "markdownify",
    "martor",
    "rest_framework",
    "sass_processor",
    "materialized_queries_manager",
    "tabsManager",
    "uploader",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "dashboard_viewer.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "shared/templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.media",
            ],
        },
    },
]

WSGI_APPLICATION = "dashboard_viewer.wsgi.application"


# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DEFAULT_DB", "cdm"),
        "HOST": os.environ.get("POSTGRES_DEFAULT_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_DEFAULT_PORT", "5432"),
        "USER": os.environ.get("POSTGRES_DEFAULT_USER", "cdm"),
        "PASSWORD": os.environ.get("POSTGRES_DEFAULT_PASSWORD", "cdm"),
    },
    "achilles": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_ACHILLES_DB", "achilles"),
        "HOST": os.environ.get("POSTGRES_ACHILLES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_ACHILLES_PORT", "5432"),
        "USER": os.environ.get("POSTGRES_ACHILLES_USER", "achilles"),
        "PASSWORD": os.environ.get("POSTGRES_ACHILLES_PASSWORD", "achilles"),
    },
}

ACHILLES_DB_SQLALCHEMY_ENGINE = create_engine(
    "postgresql"
    f"://{DATABASES['achilles']['USER']}:{DATABASES['achilles']['PASSWORD']}"
    f"@{DATABASES['achilles']['HOST']}:{DATABASES['achilles']['PORT']}"
    f"/{DATABASES['achilles']['NAME']}"
)

DATABASE_ROUTERS = ["dashboard_viewer.routers.AchillesRouter"]


# Password validation
# https://docs.djangoproject.com/en/2.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/2.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/2.2/howto/static-files/

STATIC_ROOT = os.path.join(BASE_DIR, "static")
SASS_PROCESSOR_ROOT = STATIC_ROOT
STATIC_URL = "/static/"

STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "node_modules"),
    os.path.join(BASE_DIR, "shared/static"),
]

STATICFILES_FINDERS = (
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "sass_processor.finders.CssFinder",
)

# Media files (Uploaded images, ...)
MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")


# Uploader app specific settings
ACHILLES_RESULTS_STORAGE_PATH = "achilles_results_files"


# Redis
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = os.environ.get("REDIS_PORT", 6379)
REDIS_CACHE_DB = os.environ.get("REDIS_CACHE_DB", 0)
REDIS_CELERY_DB = os.environ.get("REDIS_CELERY_DB", 1)
REDIS_CONSTANCE_DB = os.environ.get("REDIS_CONSTANCE_DB", 2)
REDIS_CELERY_WORKERS_LOCKS_DB = os.environ.get("REDIS_CELERY_WORKERS_LOCKS_DB", 5)

# Celery
CELERY_BROKER_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_CELERY_DB}"
CELERY_RESULT_BACKEND = "django-db"


# Cache
def locks_make_key(key, key_prefix, version):  # noqa
    # since locks and stuff don't require versioning, ignore those fields when building the key store name
    return key


CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_CACHE_DB}",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    },
    "workers_locks": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_CELERY_WORKERS_LOCKS_DB}",
        "KEY_FUNCTION": locks_make_key,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    },
}

# Constance
CONSTANCE_REDIS_CONNECTION = {
    "host": REDIS_HOST,
    "port": REDIS_PORT,
    "db": REDIS_CONSTANCE_DB,
}
CONSTANCE_DBS = ["default"]

CONSTANCE_ADDITIONAL_FIELDS = {
    "image": ["django.forms.ImageField", {"required": False}],
    "url": ["django.forms.URLField", {"required": False}],
    "markdown": [
        "django.forms.CharField",
        {"widget": "martor.widgets.AdminMartorWidget", "required": False},
    ],
}

CONSTANCE_CONFIG = {
    "APP_LOGO_IMAGE": ("CDM-BI-icon.png", "Image file to use as app logo.", "image"),
    "APP_LOGO_URL": (
        "",
        "Url to the image to usa as app logo."
        "This setting will be used over the APP_LOG_IMAGE",
        "url",
    ),
    "APP_TITLE": (
        "Network Dashboards",
        "Title to use for the several pages",
        str,
    ),
    "UPLOADER_EXECUTE_EXPORT_PACKAGE": (
        "The CatalogueExport package extracts all data from the CDM that is needed for the dashboards. Please run "
        "the R package (https://github.com/EHDEN/CatalogueExport) against your CDM to generate the results file.",
        "Text for the 'Execute CatalogueExport Package' section on the uploader app",
        "markdown",
    ),
    "DARWIN_EU_DASHBOARD_EXPORT_PACKAGE": (
        "DARWIN-EU DashboardExport package: https://github.com/darwin-eu/DashboardExport",
        "Text for the DARWIN-EU DashboardExport package",
        "markdown",
    ),
    "UPLOADER_UPLOAD": (
        "Upload the catalogue_results.csv results file in this tool to populate the visualisations. To update "
        "an existing database, just upload the new data. A history of uploads is shown on the page.",
        "Text for the 'Upload Achilles results' section on the uploader app",
        "markdown",
    ),
    "UPLOADER_AUTO_UPDATE": (
        "The dashboards will automatically update once the data is uploaded. This operation can take a few minutes.",
        "Text for the 'Automatic Updates' section on the uploader app",
        "markdown",
    ),
    "UPLOADER_ALLOW_EDIT_DRAFT_STATUS": (
        False,
        "If a Data Source owner can change the draft status when editing its details",
        bool,
    ),
    "SUPERSET_HOST": (
        "https://superset.ehden.eu",
        "Host of the target superset installation. Used to redirect to the dashboard of a database",
        str,
    ),
    "DATABASE_DASHBOARD_IDENTIFIER": (
        "database-level-dashboard",
        "Identifier of the database dashboard on the Superset installation.",
        str,
    ),
    "DATABASE_FILTER_ID": (
        69,
        "Id of the database filter present in the Database Dashboard",
        int,
    ),
    "TABS_LOGO_CONTAINER_CSS": (
        "padding: 5px 5px 5px 5px;\nheight: 100px;\nmargin-bottom: 10px;\n",
        "Css for the div container of the logo image",
        str,
    ),
    "TABS_LOGO_IMG_CSS": (
        "background: #fff;\n"
        "object-fit: contain;\n"
        "width: 90px;\n"
        "height: 100%;\n"
        "border-radius: 25px;\n"
        "padding: 0 5px 0 5px;\n"
        "transition: width 400ms, height 400ms;\n"
        "position: relative;\n"
        "z-index: 5;\n",
        "Css for the img tag displaying the app logo",
        str,
    ),
}

CONSTANCE_CONFIG_FIELDSETS = OrderedDict(
    [
        ("Application Attributes", ("APP_LOGO_IMAGE", "APP_LOGO_URL", "APP_TITLE")),
        (
            "Uploader Texts",
            (
                "UPLOADER_EXECUTE_EXPORT_PACKAGE",
                "UPLOADER_UPLOAD",
                "UPLOADER_AUTO_UPDATE",
                "DARWIN_EU_DASHBOARD_EXPORT_PACKAGE",
            ),
        ),
        ("Uploader Settings", ("UPLOADER_ALLOW_EDIT_DRAFT_STATUS",)),
        (
            "Superset",
            ("SUPERSET_HOST", "DATABASE_DASHBOARD_IDENTIFIER", "DATABASE_FILTER_ID"),
        ),
        ("Tabs (Deprecated)", ("TABS_LOGO_CONTAINER_CSS", "TABS_LOGO_IMG_CSS")),
    ]
)


@receiver(config_updated)
def constance_updated(key, old_value, **_):
    if key == "APP_LOGO_IMAGE" and old_value:
        try:
            os.remove(os.path.join(MEDIA_ROOT, old_value))
        except FileNotFoundError:
            pass


# Markdown editor (Martor)
MARTOR_ENABLE_CONFIGS = {
    "emoji": "false",
    "imgur": "false",
    "mention": "false",
    "jquery": "true",
    "living": "false",  # to enable/disable live updates in preview
    "spellcheck": "true",
    "hljs": "true",  # to enable/disable hljs highlighting in preview
}


TEST_RUNNER = "dashboard_viewer.runners.CeleryTestSuiteRunner"


# Variables that allow restricting the access to the uploader app if this Django
#  app is being used as a third-party tool and is being iframed.
SINGLE_APPLICATION_MODE = strtobool(os.environ.get("SINGLE_APPLICATION_MODE", "y")) == 1
MAIN_APPLICATION_HOST = os.environ.get("MAIN_APPLICATION_HOST")

if not SINGLE_APPLICATION_MODE:
    if MAIN_APPLICATION_HOST is None:
        raise ValueError(
            "If the application is not running on single application mode then the "
            "MAIN_APPLICATION_HOST variable must be defined."
        )
    if not _lazy_re_compile(URLValidator.host_re).fullmatch(MAIN_APPLICATION_HOST):
        raise ValueError(
            "The variable MAIN_APPLICATION_HOST contains an invalid hostname. "
            "Only include the hostname part of the URL."
        )

    X_FRAME_OPTIONS = f"ALLOW-FROM https://{MAIN_APPLICATION_HOST}/"

# required since django 3.2
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
