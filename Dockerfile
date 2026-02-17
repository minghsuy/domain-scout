# Build stage
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --all-extras --no-editable
COPY domain_scout/ domain_scout/
COPY README.md LICENSE ./
RUN uv sync --no-dev --frozen --all-extras --no-editable

# Runtime stage
FROM python:3.12-slim
RUN groupadd -r scout && useradd -r -g scout scout
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/pyproject.toml /app/README.md /app/LICENSE /app/
RUN mkdir -p /data/cache && chown scout:scout /data/cache
VOLUME /data/cache
ENV PATH="/app/.venv/bin:$PATH"
ENV DOMAIN_SCOUT_CACHE_DIR=/data/cache
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8080/health').raise_for_status()"
USER scout
ENTRYPOINT ["domain-scout"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
