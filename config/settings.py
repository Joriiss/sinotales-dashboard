"""
Django settings for china-blog-dashboard project.
"""

from pathlib import Path
from dotenv import load_dotenv
import os
import logging.config

env_path = Path('/srv/china_blog_dashboard/.env')
load_dotenv(env_path)

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv not installed, skip loading .env file
    pass

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-change-this-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['dashboard.joris-rabilloud.com', '127.0.0.1']


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',  # For number formatting
    'sources',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'china_blog'),
        'USER': os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', 'localhost'),  # VPS IP address or domain
        'PORT': os.environ.get('DB_PORT', '5432'),
        'OPTIONS': {
            # SSL connection options (recommended for remote connections)
            'sslmode': os.environ.get('DB_SSLMODE', 'prefer'),  # Options: disable, allow, prefer, require, verify-ca, verify-full
        },
        # Connection timeout settings
        'CONN_MAX_AGE': 600,  # Keep connections alive for 10 minutes
        'CONN_HEALTH_CHECKS': True,
    }
}


# Password validation
# https://docs.djangoproject.com/en/5.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'  # For production, where collectstatic will gather files

# Only add STATICFILES_DIRS if the directory exists
static_dir = BASE_DIR / 'static'
if static_dir.exists():
    STATICFILES_DIRS = [static_dir]

# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication settings
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'  # Redirects to dashboard
LOGOUT_REDIRECT_URL = '/login/'

# OpenAI settings for embeddings
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
OPENAI_EMBEDDING_MODEL = os.environ.get('OPENAI_EMBEDDING_MODEL', 'text-embedding-3-small')
OPENAI_EMBEDDING_DIMENSIONS = int(os.environ.get('OPENAI_EMBEDDING_DIMENSIONS', 1536))

# Web search API settings (for RAG agent)
TAVILY_API_KEY = os.environ.get('TAVILY_API_KEY', None)
SERPER_API_KEY = os.environ.get('SERPER_API_KEY', None)
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY', None)
GOOGLE_CSE_ID = os.environ.get('GOOGLE_CSE_ID', None)

# API Token for external API access
API_TOKEN = os.environ.get('API_TOKEN', '')

LOG_DIR = os.path.join(BASE_DIR, "logs")
# Try to create log directory, but don't fail if we can't
try:
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
except (OSError, PermissionError):
    pass  # Directory creation failed, will use console logging only

# Check if we can write to the log file
LOG_FILE = os.path.join(LOG_DIR, "django.log")
CAN_WRITE_LOG_FILE = False
if os.path.exists(LOG_DIR):
    try:
        # Try to create/write to the log file
        test_file = os.path.join(LOG_DIR, ".test_write")
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        CAN_WRITE_LOG_FILE = True
    except (OSError, PermissionError):
        CAN_WRITE_LOG_FILE = False

# Build handlers list - always include console, conditionally include file
handlers = {
    "console": {
        "class": "logging.StreamHandler",
        "formatter": "simple",
    },
}

root_handlers = ["console"]

if CAN_WRITE_LOG_FILE:
    handlers["file"] = {
        "level": "INFO",
        "class": "logging.FileHandler",
        "filename": LOG_FILE,
        "formatter": "verbose",
    }
    root_handlers.append("file")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": handlers,
    "root": {
        "handlers": root_handlers,
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": root_handlers,
            "level": "INFO",
            "propagate": True,
        },
        "django.request": {
            "handlers": root_handlers,
            "level": "ERROR",
            "propagate": False,
        },
    },
}