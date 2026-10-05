FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_RUNTIME_DIR=/app/runtime \
    CARDIO_PIPELINE_CONFIG=/app/config/docker.toml \
    HF_HOME=/app/hf_cache \
    HF_HUB_CACHE=/app/hf_cache/hub \
    TRANSFORMERS_CACHE=/app/hf_cache/hub \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

# Local Transformers dependencies are optional. Keep INSTALL_LOCAL=1 for the
# built-in model, or build with INSTALL_LOCAL=0 for rules/API-only deployment.
ARG INSTALL_LOCAL=1

COPY requirements-base.txt requirements-local.txt ./

RUN pip install --upgrade pip && \
    pip install -r requirements-base.txt && \
    if [ "$INSTALL_LOCAL" = "1" ]; then \
        pip install -r requirements-local.txt; \
    fi

COPY . /app

# Normalize copied-file permissions. ZIP extraction on macOS/Linux can preserve
# restrictive source modes (for example 0600), while the container deliberately
# runs as the unprivileged `app` user. Ensure application files are readable and
# directories/traversable executables retain execute permission.
RUN chmod -R a+rX /app

# Create persistent-data mount points before switching to non-root user.
# /app/hf_cache will later be backed by a Docker named volume.
RUN mkdir -p \
        /app/runtime/data \
        /app/runtime/md \
        /app/runtime/anonymization_reports \
        /app/runtime/batches \
        /app/hf_cache/hub && \
    addgroup --system app && \
    adduser --system --ingroup app app && \
    chown -R app:app /app/runtime /app/hf_cache

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()" || exit 1


# One Gunicorn process is safe for the optional in-process Transformers model:
# multiple workers would otherwise duplicate model RAM. For production scale,
# move inference to a dedicated service and scale the stateless web tier.
CMD ["gunicorn", "--no-control-socket", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "600", "app:app"]