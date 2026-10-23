"""
Development environment settings.
"""

import os

from .base import *

DEBUG = os.getenv("DJANGO_DEBUG", "True").lower() in ("true", "1", "yes")

# Ensure localhost and 127.0.0.1 are always included in development
ALLOWED_HOSTS = list(set(ALLOWED_HOSTS + ["localhost", "127.0.0.1", "0.0.0.0"]))
