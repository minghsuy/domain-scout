# Build stage
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
# Install dependencies only (cached layer — invalidated only when deps change)
RUN uv sync --no-dev --frozen --all-extras --no-editable --no-install-project
# Copy source and build the project
COPY domain_scout/ domain_scout/
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
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1
USER scout
ENTRYPOINT ["domain-scout"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
