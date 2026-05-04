FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --upgrade pip wheel \
 && pip install --prefix=/install ".[report]"

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends glpk-utils libpq5 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -r app && useradd -r -g app app

COPY --from=builder /install /usr/local
WORKDIR /app
COPY src/ ./src/
COPY config/ ./config/
COPY pyproject.toml ./
COPY alembic.ini ./
COPY alembic/ ./alembic/

USER app

ENTRYPOINT ["meal-planner"]
CMD ["--help"]
