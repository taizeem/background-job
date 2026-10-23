"""
Job processing exceptions and failure categorization.
"""


class JobError(Exception):
    """Base exception for all job execution failures."""


class RetryableJobError(JobError):
    """
    Transient failure that should trigger an automatic retry with backoff
    (e.g., network timeouts, rate limit throttles, temporary service outages).
    """


class NonRetryableJobError(JobError):
    """
    Permanent failure that should never be retried
    (e.g., invalid payload schemas, missing required data, domain constraint violations).
    """
