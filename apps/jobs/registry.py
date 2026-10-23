"""
Job handler registry and dynamic dispatch system.
"""

from collections.abc import Callable
from typing import Any

# Type signature for a job handler: receives payload dict, returns result dict
JobHandler = Callable[[dict[str, Any]], dict[str, Any]]

_REGISTRY: dict[str, JobHandler] = {}


def register_job(job_type: str) -> Callable[[JobHandler], JobHandler]:
    """
    Decorator that registers a Python function as the handler for a job_type.
    """

    def decorator(func: JobHandler) -> JobHandler:
        if job_type in _REGISTRY:
            raise ValueError(
                f"Handler for job_type '{job_type}' is already registered."
            )
        _REGISTRY[job_type] = func
        return func

    return decorator


def get_handler(job_type: str) -> JobHandler | None:
    """Returns the registered handler for the given job_type, or None if not found."""
    return _REGISTRY.get(job_type)


def is_job_type_registered(job_type: str) -> bool:
    """Returns True if a handler is registered for the given job_type."""
    return job_type in _REGISTRY


def list_registered_job_types() -> list[str]:
    """Returns a sorted list of all registered job types."""
    return sorted(_REGISTRY.keys())
