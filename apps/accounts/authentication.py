from rest_framework import authentication, exceptions
from rest_framework.request import Request

from apps.accounts.models import ApiKey


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """
    Authenticates requests using a cryptographic API Key provided via
    'X-API-Key: <token>' or 'Authorization: Bearer <token>'.
    """

    def authenticate(self, request: Request):
        raw_key = None

        # Check X-API-Key header first
        if "HTTP_X_API_KEY" in request.META:
            raw_key = request.META["HTTP_X_API_KEY"].strip()

        # Fallback: check Authorization header (Bearer or Token)
        elif "HTTP_AUTHORIZATION" in request.META:
            auth_header = request.META["HTTP_AUTHORIZATION"].split()
            if len(auth_header) == 2 and auth_header[0].lower() in (
                "bearer",
                "token",
                "apikey",
            ):
                raw_key = auth_header[1].strip()

        if not raw_key:
            return None  # No credentials provided; let permission class handle it

        # Compute SHA-256 hash and query database
        hashed = ApiKey.hash_key(raw_key)
        api_key = (
            ApiKey.objects.select_related("user")
            .filter(hashed_key=hashed, is_active=True)
            .first()
        )

        if not api_key:
            raise exceptions.AuthenticationFailed("Invalid or inactive API key.")

        # Return tuple of (user, auth_object)
        return api_key.user, api_key
