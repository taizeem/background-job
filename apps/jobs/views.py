from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

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
)


class StandardResultsSetPagination(PageNumberPagination):
    """Standard pagination configuration for job lists."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class JobListCreateView(APIView):
    """
    GET  /api/v1/jobs/ -> List jobs with optional filtering.
    POST /api/v1/jobs/ -> Submit a new background job.
    """

    authentication_classes = ()
    permission_classes = ()

    def get(self, request: Request) -> Response:
        queryset = Job.objects.all().order_by("-created_at")

        # Query filters
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

    def post(self, request: Request) -> Response:
        serializer = JobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        validated_data = serializer.validated_data
        try:
            result = JobService.create_job(
                job_type=validated_data["job_type"],
                payload=validated_data.get("payload", {}),
                priority=validated_data.get("priority"),
                max_attempts=validated_data.get("max_attempts"),
                scheduled_at=validated_data.get("scheduled_at"),
                idempotency_key=validated_data.get("idempotency_key"),
            )
        except IdempotencyConflictError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )

        response_status = (
            status.HTTP_201_CREATED if result.is_created else status.HTTP_200_OK
        )
        output_serializer = JobDetailSerializer(result.job)
        return Response(output_serializer.data, status=response_status)


class JobDetailView(APIView):
    """
    GET /api/v1/jobs/<uuid:job_id>/ -> Retrieve full details and attempts for a specific job.
    """

    authentication_classes = ()
    permission_classes = ()

    def get(self, request: Request, job_id: str) -> Response:
        job = Job.objects.filter(id=job_id).first()
        if not job:
            return Response(
                {"error": f"Job with ID '{job_id}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = JobDetailSerializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)


class JobCancelView(APIView):
    """
    POST /api/v1/jobs/<uuid:job_id>/cancel/ -> Cancel an eligible job.
    """

    authentication_classes = ()
    permission_classes = ()

    def post(self, request: Request, job_id: str) -> Response:
        try:
            job = JobService.cancel_job(job_id=job_id)
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
