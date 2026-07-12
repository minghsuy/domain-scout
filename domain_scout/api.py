"""FastAPI REST API for domain-scout."""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, get_args

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Response, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from domain_scout._metrics import CONTENT_TYPE_LATEST, generate_latest
from domain_scout.cache import DuckDBCache
from domain_scout.config import LocalMode, ProfileName, ScoutConfig
from domain_scout.delta import compute_delta
from domain_scout.models import DeltaReport, EntityInput, ScoutResult
from domain_scout.scout import Scout

try:
    import duckdb

    # DuckDB raises IOException when another process already holds the write
    # lock on cache.db (e.g. a second uvicorn worker). Catch it to enforce the
    # single-writer constraint gracefully instead of crash-looping (#169).
    _CACHE_LOCK_ERRORS: tuple[type[Exception], ...] = (duckdb.IOException,)
except ImportError:  # pragma: no cover - duckdb is an optional [cache] extra
    _CACHE_LOCK_ERRORS = ()

log = structlog.get_logger()

_VERSION = _pkg_version("domain-scout-ct")

# Semaphore acquire timeout — return 429 if exceeded
_SEMAPHORE_TIMEOUT = 10.0

# Maximum scan timeout (seconds) — cap user-supplied values
_MAX_SCAN_TIMEOUT = 300

# Readiness probe cache — avoid hammering crt.sh on every /ready call
_READY_CACHE_TTL = 60.0


def _reject_traversal(path: str, field: str) -> None:
    """Raise 400 if path contains '..' components or is an absolute path."""
    if ".." in Path(path).parts:
        raise HTTPException(status_code=400, detail=f"Invalid {field}: path traversal detected")
    if Path(path).is_absolute():
        raise HTTPException(status_code=400, detail=f"Invalid {field}: must be a relative path")


class ScanRequest(BaseModel):
    """Request body for /scan endpoint."""

    entity: EntityInput
    profile: ProfileName | None = Field(default=None, description="broad | balanced | strict")
    timeout: int | None = Field(
        default=None, ge=5, le=300, description="Override total_timeout (seconds)"
    )
    deep: bool = Field(default=False, description="Enable GeoDNS deep mode")
    local_mode: LocalMode | None = Field(
        default=None, description="disabled | local_only | local_first"
    )
    warehouse_path: str | None = Field(
        default=None, description="Path to parquet warehouse directory (requires local_mode)"
    )
    subsidiaries_path: str | None = Field(default=None, description="Path to subsidiaries CSV file")


class DiffRequest(BaseModel):
    """Request body for /diff endpoint."""

    baseline: ScoutResult
    current: ScoutResult


def create_app(
    *,
    cache: DuckDBCache | None = None,
    max_concurrent: int = 3,
    api_key: str | None = None,
    default_warehouse_path: str | None = None,
    default_subsidiaries_path: str | None = None,
    default_local_mode: LocalMode = "disabled",
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
    app.state.api_key = api_key
    app.state.default_warehouse_path = default_warehouse_path
    app.state.default_subsidiaries_path = default_subsidiaries_path
    app.state.default_local_mode = default_local_mode

    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
    bearer_scheme = HTTPBearer(auto_error=False)

    def verify_api_key(
        api_key_header: str | None = Security(api_key_header),
        bearer: HTTPAuthorizationCredentials | None = Security(bearer_scheme),  # noqa: B008
    ) -> None:
        # X-API-Key takes precedence (existing primary path); Bearer is the
        # fallback for clients that forward Authorization but not custom headers.
        if app.state.api_key is None:
            return
        # `is not None` (not truthy `or`) is defensive: in practice FastAPI's
        # APIKeyHeader returns None for empty/absent headers, but this makes
        # the precedence explicit and future-proof if that ever changes.
        resolved_key = (
            api_key_header
            if api_key_header is not None
            else (bearer.credentials if bearer else None)
        )
        if not resolved_key:
            raise HTTPException(status_code=401, detail="API Key required")
        if not secrets.compare_digest(resolved_key, app.state.api_key):
            raise HTTPException(status_code=401, detail="Invalid API Key")

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

    @app.post("/scan", response_model=ScoutResult, dependencies=[Depends(verify_api_key)])
    async def scan(req: ScanRequest) -> ScoutResult:
        """Run a domain discovery scan."""
        overrides: dict[str, Any] = {}
        if req.timeout is not None:
            # Pydantic le=300 already caps, min() is defense-in-depth
            overrides["total_timeout"] = min(req.timeout, _MAX_SCAN_TIMEOUT)
        if req.deep:
            overrides["deep_mode"] = True

        # Resolve local_mode: request overrides server default
        local_mode = req.local_mode if req.local_mode is not None else app.state.default_local_mode
        if local_mode != "disabled":
            overrides["local_mode"] = local_mode
            if req.warehouse_path:
                _reject_traversal(req.warehouse_path, "warehouse_path")
                overrides["warehouse_path"] = req.warehouse_path
            elif app.state.default_warehouse_path:
                overrides["warehouse_path"] = app.state.default_warehouse_path

        # Resolve subsidiaries_path: request overrides server default
        if req.subsidiaries_path:
            _reject_traversal(req.subsidiaries_path, "subsidiaries_path")
            overrides["subsidiaries_path"] = req.subsidiaries_path
        elif app.state.default_subsidiaries_path:
            overrides["subsidiaries_path"] = app.state.default_subsidiaries_path

        try:
            if req.profile:
                config = ScoutConfig.from_profile(req.profile, **overrides)
            else:
                config = ScoutConfig(**overrides)
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

        # Scout construction can open DuckDB connections and scan a parquet
        # warehouse (local mode) — run it in an executor so it can't block
        # the event loop, and always close it to release those connections.
        scout: Scout | None = None
        loop = asyncio.get_running_loop()
        try:
            scout = await loop.run_in_executor(
                None, lambda: Scout(config=config, cache=app.state.cache)
            )
            result = await scout.discover_async(req.entity)
        except Exception as exc:
            log.error("scan.failed", error=str(exc), entity=req.entity.company_name)
            raise HTTPException(status_code=500, detail="Internal scan error") from exc
        finally:
            if scout is not None:
                await loop.run_in_executor(None, scout.close)
            app.state.semaphore.release()

        return result

    @app.get("/cache/stats", dependencies=[Depends(verify_api_key)])
    async def cache_stats() -> dict[str, Any]:
        """Return cache statistics."""
        if app.state.cache is None:
            return {"enabled": False}
        loop = asyncio.get_running_loop()
        stats = await loop.run_in_executor(None, app.state.cache.stats)
        return {"enabled": True, **stats}

    @app.post("/cache/clear", dependencies=[Depends(verify_api_key)])
    async def cache_clear() -> dict[str, str]:
        """Clear all cached entries."""
        if app.state.cache is None:
            raise HTTPException(status_code=400, detail="Cache is not enabled")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, app.state.cache.clear)
        return {"status": "cleared"}

    # No semaphore needed: compute_delta is pure CPU, no network I/O,
    # sub-millisecond for typical result sizes (<100 domains).
    @app.post("/diff", response_model=DeltaReport, dependencies=[Depends(verify_api_key)])
    async def diff_endpoint(req: DiffRequest) -> DeltaReport:
        """Compute delta between two scan results."""
        return compute_delta(req.baseline, req.current)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        body = generate_latest()
        media = CONTENT_TYPE_LATEST or "text/plain; charset=utf-8"
        return Response(content=body, media_type=media)

    return app


def get_app() -> FastAPI:
    """Factory for default app (used by uvicorn import string).

    DuckDB is single-writer, so only one process may open ``cache.db`` for
    writing. This factory hardens the two documented foot-guns (#169):

    * **Multi-worker cache.** The CLI ``serve`` command disables the cache when
      ``--workers > 1``. If you instead run ``uvicorn domain_scout.api:get_app``
      directly with multiple workers, each worker calls this factory; the first
      opens ``cache.db`` and the rest hit a DuckDB lock conflict. Rather than
      crash-loop, a worker that loses the race logs
      ``api.cache_disabled_lock_conflict`` and starts cache-less. Set
      ``DOMAIN_SCOUT_CACHE=false`` to disable the cache explicitly and silence
      the warning.
    * **Bad env values.** A non-numeric ``DOMAIN_SCOUT_MAX_CONCURRENT`` logs a
      warning and falls back to the default (3) instead of crashing at startup.
    * **Missing [cache] extra.** A base install lacks ``duckdb``, yet
      ``DOMAIN_SCOUT_CACHE`` still defaults to enabled. Rather than crash-loop
      with ``ImportError`` at startup, the factory logs
      ``api.cache_disabled_missing_extra`` and starts cache-less. Install
      ``domain-scout-ct[cache]`` to enable caching (#193).
    """
    raw_max_concurrent = os.environ.get("DOMAIN_SCOUT_MAX_CONCURRENT", "3")
    try:
        max_concurrent = int(raw_max_concurrent)
    except ValueError:
        log.warning("get_app.invalid_max_concurrent_env", value=raw_max_concurrent)
        max_concurrent = 3
    if max_concurrent < 1:
        log.warning("get_app.invalid_max_concurrent", value=max_concurrent)
        max_concurrent = 1
    cache_enabled = os.environ.get("DOMAIN_SCOUT_CACHE", "true").lower() != "false"

    cache_dir = os.environ.get("DOMAIN_SCOUT_CACHE_DIR")
    cache: DuckDBCache | None = None
    if cache_enabled:
        try:
            cache = DuckDBCache(cache_dir=cache_dir)
        except ImportError as exc:
            # Base install without the [cache] extra: DuckDBCache.__init__ raises
            # ImportError (duckdb absent) from a single guard. With the cache
            # extra uninstalled DOMAIN_SCOUT_CACHE still defaults to "true", so
            # `serve` (workers=1) and `uvicorn ...:get_app` would crash-loop.
            # Degrade to cache-less rather than crash; the message names the
            # extra to install (#193).
            log.warning("api.cache_disabled_missing_extra", error=str(exc))
            cache = None
        except _CACHE_LOCK_ERRORS as exc:
            # Another writer already holds cache.db (single-writer constraint).
            # Degrade to cache-less rather than corrupt the file or crash-loop.
            log.warning("api.cache_disabled_lock_conflict", error=str(exc))
            cache = None

    api_key = os.environ.get("DOMAIN_SCOUT_API_KEY")
    warehouse_path = os.environ.get("DOMAIN_SCOUT_WAREHOUSE_PATH")
    subsidiaries_path = os.environ.get("DOMAIN_SCOUT_SUBSIDIARIES_PATH")

    # Auto-enable local_first when warehouse path is configured
    default_local_mode: LocalMode = "disabled"
    raw_mode = os.environ.get("DOMAIN_SCOUT_LOCAL_MODE")
    if raw_mode in get_args(LocalMode):
        default_local_mode = cast("LocalMode", raw_mode)
    elif warehouse_path:
        default_local_mode = "local_first"

    return create_app(
        cache=cache,
        max_concurrent=max_concurrent,
        api_key=api_key,
        default_warehouse_path=warehouse_path,
        default_subsidiaries_path=subsidiaries_path,
        default_local_mode=default_local_mode,
    )
