"""
Registry of supported job types.
Clients may only submit jobs whose job_type has been explicitly registered.
"""

from typing import Final

# Supported job types for the platform
ALLOWED_JOB_TYPES: Final[frozenset[str]] = frozenset(
    {
        "reports.generate",
        "email.send",
        "files.process",
        "webhooks.deliver",
        "maintenance.cleanup",
    }
)


def is_job_type_registered(job_type: str) -> bool:
    """Returns True if the given job_type is recognized by the platform."""
    return job_type in ALLOWED_JOB_TYPES
