"""
Production environment settings with security hardening.
"""

import os

from .base import *

DEBUG = False

# Require explicit secret key in production
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

# Security and HTTPS/TLS headers (configurable via env for local Docker testing vs cloud LB)
SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL_REDIRECT", "True").lower() in (
    "true",
    "1",
    "yes",
)
SESSION_COOKIE_SECURE = os.getenv("DJANGO_SESSION_COOKIE_SECURE", "True").lower() in (
    "true",
    "1",
    "yes",
)
CSRF_COOKIE_SECURE = os.getenv("DJANGO_CSRF_COOKIE_SECURE", "True").lower() in (
    "true",
    "1",
    "yes",
)

SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "31536000"))  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Trust X-Forwarded-Proto from upstream reverse proxy / load balancer
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
