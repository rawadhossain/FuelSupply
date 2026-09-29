# syntax=docker/dockerfile:1
# Build context: repo root (needs ./shared)
FROM python:3.11-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependencies first: this layer only rebuilds when requirements change.
COPY intelligence/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# Non-editable install so the runtime image needs no source tree for shared.
COPY shared /tmp/shared
RUN pip install /tmp/shared

FROM python:3.11-slim-bookworm AS runtime

ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA     PYTHONUNBUFFERED=1     PYTHONDONTWRITEBYTECODE=1     PATH="/opt/venv/bin:$PATH"

RUN groupadd --system --gid 10001 app  && useradd --system --uid 10001 --gid app --no-create-home app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# Whole package (not just a subfolder): service.py, the model/forecast/detect/
# narrate/policy modules, and artifacts/profile-v1 (the trained model files
# Assessor loads at startup) all live under intelligence/ as one import root.
COPY --chown=app:app intelligence ./intelligence

USER app
EXPOSE 8200

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5   CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8200/health', timeout=2)"]

CMD ["uvicorn", "intelligence.service:app", "--host", "0.0.0.0", "--port", "8200"]
