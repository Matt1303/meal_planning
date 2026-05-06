FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --upgrade pip wheel \
 && pip install ".[report]"

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
 && apt-get install -y --no-install-recommends glpk-utils libpq5 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -r app && useradd -r -g app -d /home/app -m app

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY src/ ./src/
COPY config/ ./config/
COPY pyproject.toml README.md ./
COPY alembic.ini ./
COPY alembic/ ./alembic/

ENV MPLCONFIGDIR=/tmp/matplotlib HOME=/home/app
USER app

ENTRYPOINT ["meal-planner"]
CMD ["--help"]
