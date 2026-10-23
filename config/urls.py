"""
Root URL router for the job platform.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", include("apps.core.urls")),
    path("api/v1/jobs/", include("apps.jobs.urls")),
]
