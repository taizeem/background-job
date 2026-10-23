from django.urls import path

from apps.jobs.views import JobCancelView, JobDetailView, JobListCreateView

urlpatterns = [
    path("", JobListCreateView.as_view(), name="job-list-create"),
    path("<uuid:job_id>/", JobDetailView.as_view(), name="job-detail"),
    path("<uuid:job_id>/cancel/", JobCancelView.as_view(), name="job-cancel"),
]
