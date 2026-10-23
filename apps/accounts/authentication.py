from rest_framework import authentication, exceptions
from rest_framework.request import Request

from apps.accounts.models import ApiKey


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """
    Authenticates requests using a cryptographic API Key provided via
    'X-API-Key: <token>' or 'Authorization: Bearer <token>'.
    """

    def authenticate_header(self, request: Request) -> str:
        """
        Enables standard HTTP 401 Unauthorized challenges with WWW-Authenticate header.
        """
        return 'Bearer realm="api"'

    def authenticate(self, request: Request):
        raw_key = None

        if "HTTP_X_API_KEY" in request.META:
            raw_key = request.META["HTTP_X_API_KEY"].strip()
        elif "HTTP_AUTHORIZATION" in request.META:
            auth_header = request.META["HTTP_AUTHORIZATION"].split()
            if len(auth_header) == 2 and auth_header[0].lower() in ("bearer", "token", "apikey"):
                raw_key = auth_header[1].strip()

        if not raw_key:
            return None

        hashed = ApiKey.hash_key(raw_key)
        api_key = (
            ApiKey.objects.select_related("user")
            .filter(hashed_key=hashed, is_active=True)
            .first()
        )

        if not api_key:
            raise exceptions.AuthenticationFailed("Invalid or inactive API key.")

        return api_key.user, api_key