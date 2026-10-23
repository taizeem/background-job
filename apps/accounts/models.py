import hashlib
import secrets
import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models


class ApiKey(models.Model):
    """
    Cryptographic API Key for client authentication.
    Only the SHA-256 hash of the key is stored in PostgreSQL.
    """

    KEY_PREFIX: ClassVar[str] = "jp_live_"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys",
        help_text="The user/tenant who owns this API key",
    )
    name = models.CharField(
        max_length=64,
        help_text="Human-readable label for this key (e.g. 'Production Backend')",
    )
    key_prefix = models.CharField(
        max_length=16,
        help_text="First few characters of the key for identification in logs/UI",
    )
    hashed_key = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 hash of the raw token",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Inactive keys are rejected immediately",
    )
    max_active_jobs = models.PositiveIntegerField(
        default=10,
        help_text="Maximum concurrent active jobs permitted for this API key",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = (
            models.Index(fields=["hashed_key", "is_active"], name="idx_api_key_lookup"),
        )

    @classmethod
    def hash_key(cls, raw_key: str) -> str:
        """Computes the SHA-256 digest of a raw key."""
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @classmethod
    def generate(
        cls, user, name: str, max_active_jobs: int = 10
    ) -> tuple["ApiKey", str]:
        """
        Generates a new ApiKey instance and returns a tuple of (ApiKey, raw_secret_key).
        The raw secret key is only available at generation time and cannot be retrieved later.
        """
        raw_secret = f"{cls.KEY_PREFIX}{secrets.token_urlsafe(32)}"
        hashed = cls.hash_key(raw_secret)
        prefix = raw_secret[:12]

        api_key = cls.objects.create(
            user=user,
            name=name,
            key_prefix=prefix,
            hashed_key=hashed,
            max_active_jobs=max_active_jobs,
        )
        return api_key, raw_secret

    def __str__(self) -> str:
        return f"ApiKey {self.name} ({self.key_prefix}...) - User {self.user_id}"
