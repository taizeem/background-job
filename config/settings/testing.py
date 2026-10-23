"""
Testing environment settings.
"""

from .base import *

DEBUG = False

# Use fast password hasher in tests to speed up test execution significantly
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Ensure tests use an isolated database name prefix
DATABASES["default"]["TEST"] = {
    "NAME": "test_job_platform",
}
