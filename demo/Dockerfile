# Sentinel web demo - container image for hosting.
#
# Build from the repository root:
#   docker build -f demo/Dockerfile -t sentinel-demo .
#   docker run -p 8080:8080 sentinel-demo
#
# The image runs the demo in --public mode: URL-only input, a concurrency cap,
# per-client rate limiting, and a bounded cache. See docs/DEMO.md.

FROM python:3.12-slim

# git is required at runtime to clone the repositories users paste.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin sentinel

WORKDIR /app

# Only what the server needs: the demo and the package it drives.
COPY --chown=sentinel:sentinel demo/server.py ./demo/server.py
COPY --chown=sentinel:sentinel src/sentinel2 ./src/sentinel2

USER sentinel

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"

CMD ["python", "demo/server.py", "--public"]