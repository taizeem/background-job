import logging

from django.db import connection
from django.db.utils import OperationalError
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.redis import is_redis_available

logger = logging.getLogger(__name__)


class HealthLiveView(APIView):
    """
    Liveness probe: returns 200 if the application process is running.
    Does not verify external dependencies.
    """

    authentication_classes = ()
    permission_classes = ()

    @extend_schema(
        summary="Liveness Probe",
        description="Returns HTTP 200 if the web server process is alive.",
        responses={200: OpenApiResponse(description="Process is healthy")},
        tags=["Health"],
    )
    def get(self, request: Request) -> Response:
        return Response({"status": "healthy"}, status=status.HTTP_200_OK)


class HealthReadyView(APIView):
    """
    Readiness probe: returns 200 only if PostgreSQL is operational.
    Also reports Redis cache connectivity status.
    """

    authentication_classes = ()
    permission_classes = ()

    @extend_schema(
        summary="Readiness Probe",
        description="Verifies connectivity to PostgreSQL and reports Redis health.",
        responses={
            200: OpenApiResponse(description="Service and database are ready"),
            503: OpenApiResponse(
                description="Critical database dependency unreachable"
            ),
        },
        tags=["Health"],
    )
    def get(self, request: Request) -> Response:
        redis_status = "connected" if is_redis_available() else "unreachable"
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                cursor.fetchone()
            return Response(
                {
                    "status": "ready",
                    "dependencies": {
                        "database": "connected",
                        "redis": redis_status,
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
                        "redis": redis_status,
                    },
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
