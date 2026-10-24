from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.authentication import ApiKeyAuthentication
from apps.accounts.throttling import RedisRateThrottle
from apps.jobs.constants import JobStatus
from apps.jobs.models import Job
from apps.jobs.serializers import (
    JobCreateSerializer,
    JobDetailSerializer,
    JobListSerializer,
)
from apps.jobs.services.job_service import (
    IdempotencyConflictError,
    JobCancellationError,
    JobService,
    PayloadTooLargeError,
    QuotaExceededError,
)
from apps.jobs.services.metrics_service import MetricsService


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class JobMetricsView(APIView):
    """
    GET /api/v1/jobs/metrics/ -> Sub-millisecond queue telemetry cached in Redis.
    """

    authentication_classes = (ApiKeyAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(
        summary="Get Queue & Worker Telemetry",
        description="Returns queue depths, active worker count, and attempt latency metrics.",
        responses={200: OpenApiResponse(description="Platform telemetry metrics")},
        tags=["Telemetry"],
    )
    def get(self, request: Request) -> Response:
        metrics = MetricsService.get_queue_metrics()
        return Response(metrics, status=status.HTTP_200_OK)


class JobListCreateView(APIView):
    """
    GET  /api/v1/jobs/ -> List jobs owned by the authenticated user.
    POST /api/v1/jobs/ -> Submit a new background job with Redis rate limiting.
    """

    authentication_classes = (ApiKeyAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)
    throttle_classes = (RedisRateThrottle,)

    @extend_schema(
        summary="List Jobs",
        description="Lists jobs belonging to the authenticated tenant with optional filtering.",
        parameters=[
            OpenApiParameter(
                name="status",
                description="Filter by job status",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="job_type",
                description="Filter by job_type",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="priority",
                description="Filter by integer priority",
                required=False,
                type=int,
            ),
        ],
        responses={200: JobListSerializer(many=True)},
        tags=["Jobs"],
    )
    def get(self, request: Request) -> Response:
        queryset = Job.objects.all().order_by("-created_at")
        if not request.user.is_staff:
            queryset = queryset.filter(user=request.user)

        status_param = request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param.upper())

        job_type_param = request.query_params.get("job_type")
        if job_type_param:
            queryset = queryset.filter(job_type=job_type_param)

        priority_param = request.query_params.get("priority")
        if priority_param and priority_param.isdigit():
            queryset = queryset.filter(priority=int(priority_param))

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = JobListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        summary="Submit a Background Job",
        description="Enqueues a new background job with optional idempotency key and future schedule.",
        request=JobCreateSerializer,
        responses={
            201: JobDetailSerializer,
            200: JobDetailSerializer,
            409: OpenApiResponse(description="Idempotency key conflict"),
            413: OpenApiResponse(description="Payload exceeds 64 KB limit"),
            429: OpenApiResponse(description="Active job quota or rate limit exceeded"),
        },
        tags=["Jobs"],
    )
    def post(self, request: Request) -> Response:
        serializer = JobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        validated_data = serializer.validated_data
        api_key = request.auth
        max_quota = api_key.max_active_jobs if api_key else 10

        try:
            result = JobService.create_job(
                user=request.user,
                job_type=validated_data["job_type"],
                payload=validated_data.get("payload", {}),
                priority=validated_data.get("priority"),
                max_attempts=validated_data.get("max_attempts"),
                scheduled_at=validated_data.get("scheduled_at"),
                idempotency_key=validated_data.get("idempotency_key"),
                max_active_jobs=max_quota,
            )
        except QuotaExceededError as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        except PayloadTooLargeError as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            )
        except IdempotencyConflictError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)

        response_status = (
            status.HTTP_201_CREATED if result.is_created else status.HTTP_200_OK
        )
        output_serializer = JobDetailSerializer(result.job)
        return Response(output_serializer.data, status=response_status)


class JobDetailView(APIView):
    """
    GET /api/v1/jobs/<uuid:job_id>/ -> Retrieve full details for a tenant-owned job.
    """

    authentication_classes = (ApiKeyAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(
        summary="Get Job Details",
        description="Retrieves complete job state, output result, and execution attempt history.",
        responses={
            200: JobDetailSerializer,
            404: OpenApiResponse(description="Job not found"),
        },
        tags=["Jobs"],
    )
    def get(self, request: Request, job_id: str) -> Response:
        queryset = Job.objects.filter(id=job_id)
        if not request.user.is_staff:
            queryset = queryset.filter(user=request.user)

        job = queryset.first()
        if not job:
            return Response(
                {"error": f"Job with ID '{job_id}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = JobDetailSerializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)


class JobCancelView(APIView):
    """
    POST /api/v1/jobs/<uuid:job_id>/cancel/ -> Cancel an eligible tenant-owned job.
    """

    authentication_classes = (ApiKeyAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(
        summary="Cancel a Job",
        description="Cancels a PENDING or RETRYING job. Running or terminal jobs cannot be cancelled.",
        request=None,
        responses={
            200: JobDetailSerializer,
            404: OpenApiResponse(description="Job not found"),
            409: OpenApiResponse(
                description="Job is running or already in a terminal state"
            ),
        },
        tags=["Jobs"],
    )
    def post(self, request: Request, job_id: str) -> Response:
        try:
            job = JobService.cancel_job(job_id=job_id, user=request.user)
        except Job.DoesNotExist:
            return Response(
                {"error": f"Job with ID '{job_id}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except JobCancellationError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = JobDetailSerializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)


class JobRetryView(APIView):
    """
    POST /api/v1/jobs/<uuid:job_id>/retry/ -> Manually re-queues an EXHAUSTED or FAILED job.
    """

    authentication_classes = (ApiKeyAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(
        summary="Replay an Exhausted or Failed Job",
        description="Resets attempt_count to 0 and transitions an EXHAUSTED or FAILED job back to PENDING.",
        request=None,
        responses={
            200: JobDetailSerializer,
            404: OpenApiResponse(description="Job not found"),
            409: OpenApiResponse(description="Job is not in EXHAUSTED or FAILED state"),
        },
        tags=["Jobs"],
    )
    def post(self, request: Request, job_id: str) -> Response:
        queryset = Job.objects.filter(id=job_id)
        if not request.user.is_staff:
            queryset = queryset.filter(user=request.user)

        job = queryset.first()
        if not job:
            return Response(
                {"error": f"Job with ID '{job_id}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if job.status not in (JobStatus.EXHAUSTED, JobStatus.FAILED):
            return Response(
                {
                    "error": f"Only EXHAUSTED or FAILED jobs can be revived. Current status is '{job.status}'."
                },
                status=status.HTTP_409_CONFLICT,
            )

        job.status = JobStatus.PENDING
        job.attempt_count = 0
        job.error_message = None
        job.error_traceback = None
        job.completed_at = None
        job.scheduled_at = timezone.now()
        job.next_retry_at = None
        job.save(
            update_fields=[
                "status",
                "attempt_count",
                "error_message",
                "error_traceback",
                "completed_at",
                "scheduled_at",
                "next_retry_at",
                "updated_at",
            ]
        )

        serializer = JobDetailSerializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)
