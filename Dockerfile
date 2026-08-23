# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip \
    && pip install .

FROM builder AS test

COPY tests ./tests
RUN pip install ".[dev]"

CMD ["pytest"]

FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

RUN groupadd --gid 10001 blogops \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin blogops

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=blogops:blogops alembic.ini ./
COPY --chown=blogops:blogops migrations ./migrations
COPY --chown=blogops:blogops src ./src

USER 10001:10001
EXPOSE 8000

CMD ["uvicorn", "blogops.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
