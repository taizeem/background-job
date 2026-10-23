"""
Loads all job handler modules to ensure registration decorators execute.
"""

from apps.jobs.handlers import email, files, maintenance, reports

__all__ = ["email", "files", "maintenance", "reports"]
