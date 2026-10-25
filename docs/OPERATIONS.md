# Production Operations & Incident Runbook

## 1. Pre-Deployment Security Checklist

Before deploying to production, verify the following environment variables are set:
- `DJANGO_SETTINGS_MODULE=config.settings.production`
- `DJANGO_SECRET_KEY`: A minimum 50-character cryptographically random secret.
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS`: Comma-separated list of production domain names.
- `DJANGO_SECURE_SSL_REDIRECT=True` (when TLS is terminated at the load balancer, ensure `X-Forwarded-Proto: https` is passed).

Run Django's deployment security audit:
```powershell
uv run python manage.py check --deploy --settings=config.settings.production