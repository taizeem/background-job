from django.urls import path

from apps.jobs.views import (
    JobCancelView,
    JobDetailView,
    JobListCreateView,
    JobMetricsView,
    JobRetryView,
)

urlpatterns = [
    path("", JobListCreateView.as_view(), name="job-list-create"),
    path("metrics/", JobMetricsView.as_view(), name="job-metrics"),
    path("<uuid:job_id>/", JobDetailView.as_view(), name="job-detail"),
    path("<uuid:job_id>/cancel/", JobCancelView.as_view(), name="job-cancel"),
    path("<uuid:job_id>/retry/", JobRetryView.as_view(), name="job-retry"),
]
