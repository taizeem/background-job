import logging

from django.db import connection
from django.db.utils import OperationalError
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class HealthLiveView(APIView):
    """
    Liveness probe: returns 200 if the application process is running.
    Does not verify external dependencies.
    """

    authentication_classes = ()
    permission_classes = ()

    def get(self, request: Request) -> Response:
        return Response({"status": "healthy"}, status=status.HTTP_200_OK)


class HealthReadyView(APIView):
    """
    Readiness probe: returns 200 only if the application and its critical
    dependencies (PostgreSQL) are operational and accepting queries.
    """

    authentication_classes = ()
    permission_classes = ()

    def get(self, request: Request) -> Response:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                cursor.fetchone()
            return Response(
                {
                    "status": "ready",
                    "dependencies": {
                        "database": "connected",
                    },
                },
                status=status.HTTP_200_OK,
            )
        except OperationalError as exc:
            logger.error("Readiness check failed: Database unreachable: %s", exc)
            return Response(
                {
                    "status": "unavailable",
                    "dependencies": {
                        "database": "unreachable",
                    },
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
