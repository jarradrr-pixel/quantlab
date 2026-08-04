# Application image. Runs as a non-root user; holds no credentials in layers.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

RUN groupadd --system quantlab && useradd --system --gid quantlab --home /srv quantlab

# app/ and alembic/ must exist before `pip install .` -- setuptools is told
# packages = ["app", "app.agents", ...] in pyproject.toml and fails the
# build if that directory isn't present yet at install time.
COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --upgrade pip && pip install .

# WORKDIR itself is created (and COPY'd into) as root; chown it, not just
# the files, so the sqlite fallback can create quantlab.db here as the
# non-root user below.
RUN chown -R quantlab:quantlab /srv

USER quantlab

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
