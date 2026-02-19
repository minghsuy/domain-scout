"""FastAPI REST API for domain-scout."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time
from contextlib import asynccontextmanager
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import httpx
import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from domain_scout.cache import DuckDBCache
from domain_scout.config import ProfileName, ScoutConfig
from domain_scout.delta import compute_delta
from domain_scout.models import DeltaReport, EntityInput, ScoutResult
from domain_scout.scout import Scout

log = structlog.get_logger()

_VERSION = _pkg_version("domain-scout-ct")

# Semaphore acquire timeout — return 429 if exceeded
_SEMAPHORE_TIMEOUT = 10.0

# Maximum scan timeout (seconds) — cap user-supplied values
_MAX_SCAN_TIMEOUT = 60

# Readiness probe cache — avoid hammering crt.sh on every /ready call
_READY_CACHE_TTL = 60.0


class ScanRequest(BaseModel):
    """Request body for /scan endpoint."""

    entity: EntityInput
    profile: ProfileName | None = Field(default=None, description="broad | balanced | strict")
    timeout: int | None = Field(
        default=None, ge=5, le=60, description="Override total_timeout (seconds)"
    )
    deep: bool = Field(default=False, description="Enable GeoDNS deep mode")


class DiffRequest(BaseModel):
    """Request body for /diff endpoint."""

    baseline: ScoutResult
    current: ScoutResult


def create_app(
    *,
    cache: DuckDBCache | None = None,
    max_concurrent: int = 3,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if app.state.cache is not None:
            app.state.cache.close()
            log.info("api.cache_closed")

    app = FastAPI(
        title="domain-scout",
        version=_VERSION,
        description="Discover internet domains associated with a business entity.",
        lifespan=lifespan,
    )

    app.state.cache = cache
    app.state.semaphore = asyncio.Semaphore(max_concurrent)
    app.state.ready_cache = {}
    app.state.ready_cache_ts = 0.0

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": _VERSION}

    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        """Check upstream connectivity (crt.sh JSON API probe)."""
        now = time.monotonic()
        cached: dict[str, Any] = app.state.ready_cache
        if cached and now - float(app.state.ready_cache_ts) < _READY_CACHE_TTL:
            return cached

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get("https://crt.sh/?output=json&q=example.com&limit=1")
                crt_sh = "ok" if resp.status_code == 200 else f"http_{resp.status_code}"
        except Exception as exc:
            log.warning("ready.crt_sh_error", error=str(exc))
            crt_sh = "unreachable"

        status = "ready" if crt_sh == "ok" else "degraded"
        result: dict[str, Any] = {
            "status": status,
            "version": _VERSION,
            "details": {"crt_sh": crt_sh},
        }
        app.state.ready_cache = result
        app.state.ready_cache_ts = now
        return result

    @app.post("/scan", response_model=ScoutResult)
    async def scan(req: ScanRequest) -> ScoutResult:
        """Run a domain discovery scan."""
        overrides: dict[str, Any] = {}
        if req.timeout is not None:
            # Pydantic le=60 already caps, min() is defense-in-depth
            overrides["total_timeout"] = min(req.timeout, _MAX_SCAN_TIMEOUT)
        if req.deep:
            overrides["deep_mode"] = True

        try:
            if req.profile:
                config = ScoutConfig.from_profile(req.profile, **overrides)
            else:
                config = ScoutConfig(**overrides)

            # Enforce hard cap on timeout, even if profile/default is higher
            if config.total_timeout > _MAX_SCAN_TIMEOUT:
                config = dataclasses.replace(config, total_timeout=_MAX_SCAN_TIMEOUT)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Acquire semaphore with timeout — return 429 if all slots busy
        try:
            async with asyncio.timeout(_SEMAPHORE_TIMEOUT):
                await app.state.semaphore.acquire()
        except TimeoutError as exc:
            raise HTTPException(
                status_code=429, detail="Too many concurrent scans, try again later"
            ) from exc

        try:
            scout = Scout(config=config, cache=app.state.cache)
            result = await scout.discover_async(req.entity)
        except Exception as exc:
            log.error("scan.failed", error=str(exc), entity=req.entity.company_name)
            raise HTTPException(status_code=500, detail="Internal scan error") from exc
        finally:
            app.state.semaphore.release()

        return result

    @app.get("/cache/stats")
    async def cache_stats() -> dict[str, Any]:
        """Return cache statistics."""
        if app.state.cache is None:
            return {"enabled": False}
        loop = asyncio.get_running_loop()
        stats = await loop.run_in_executor(None, app.state.cache.stats)
        return {"enabled": True, **stats}

    @app.post("/cache/clear")
    async def cache_clear() -> dict[str, str]:
        """Clear all cached entries."""
        if app.state.cache is None:
            raise HTTPException(status_code=400, detail="Cache is not enabled")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, app.state.cache.clear)
        return {"status": "cleared"}

    # No semaphore needed: compute_delta is pure CPU, no network I/O,
    # sub-millisecond for typical result sizes (<100 domains).
    @app.post("/diff", response_model=DeltaReport)
    async def diff_endpoint(req: DiffRequest) -> DeltaReport:
        """Compute delta between two scan results."""
        return compute_delta(req.baseline, req.current)

    return app


def get_app() -> FastAPI:
    """Factory for default app (used by uvicorn import string).

    Note: DuckDB is single-writer. The CLI ``serve`` command disables cache
    when ``--workers > 1``.  If you call ``uvicorn domain_scout.api:get_app``
    directly with multiple workers, set ``DOMAIN_SCOUT_CACHE=false``.
    """
    max_concurrent = int(os.environ.get("DOMAIN_SCOUT_MAX_CONCURRENT", "3"))
    if max_concurrent < 1:
        log.warning("get_app.invalid_max_concurrent", value=max_concurrent)
        max_concurrent = 1
    cache_enabled = os.environ.get("DOMAIN_SCOUT_CACHE", "true").lower() != "false"

    cache_dir = os.environ.get("DOMAIN_SCOUT_CACHE_DIR")
    cache = DuckDBCache(cache_dir=cache_dir) if cache_enabled else None
    return create_app(cache=cache, max_concurrent=max_concurrent)
