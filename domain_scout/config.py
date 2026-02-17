"""Configuration constants and defaults."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Literal

ProfileName = Literal["broad", "balanced", "strict"]

_PROFILES: dict[str, dict[str, object]] = {
    "broad": {
        "org_match_threshold": 0.50,
        "inclusion_threshold": 0.40,
        "seed_confirm_threshold": 0.45,
        "include_non_resolving": True,
    },
    "balanced": {},  # all defaults
    "strict": {
        "org_match_threshold": 0.80,
        "inclusion_threshold": 0.75,
        "seed_confirm_threshold": 0.75,
    },
}


@dataclass(frozen=True)
class ScoutConfig:
    """All tunables for a scout run."""

    # --- Timeouts (seconds) ---
    postgres_timeout: int = 15
    http_timeout: int = 15
    dns_timeout: float = 5.0
    total_timeout: int = 90

    # --- Retries ---
    postgres_max_retries: int = 2
    http_max_retries: int = 2

    # --- Rate limiting ---
    max_concurrent_queries: int = 5
    burst_delay: float = 1.0

    # --- Confidence thresholds ---
    seed_confirm_threshold: float = 0.6
    org_match_threshold: float = 0.65
    inclusion_threshold: float = 0.6

    # --- CT search ---
    ct_recent_years: int = 2
    ct_max_results: int = 200

    # --- crt.sh connection ---
    crtsh_postgres_host: str = "crt.sh"
    crtsh_postgres_port: int = 5432
    crtsh_postgres_db: str = "certwatch"
    crtsh_postgres_user: str = "guest"
    crtsh_json_base_url: str = "https://crt.sh"

    # --- Domain guessing ---
    guess_tlds: tuple[str, ...] = (".com", ".net", ".io", ".co", ".org")

    # --- Infrastructure checks ---
    infra_check_max: int = 10

    # --- DNS ---
    dns_nameservers: list[str] = field(default_factory=lambda: ["8.8.8.8", "1.1.1.1"])

    # --- Deep mode (GeoDNS) ---
    deep_mode: bool = False
    geodns_base_url: str = "https://geonet.shodan.io/api/geodns"
    geodns_concurrency: int = 3
    geodns_delay: float = 0.5  # seconds between requests per concurrent slot

    # --- Output filtering ---
    include_non_resolving: bool = False

    # --- Cache ---
    cache_enabled: bool = False
    cache_dir: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize config to a plain dict for audit snapshots."""
        return dataclasses.asdict(self)

    @classmethod
    def from_profile(cls, profile: ProfileName, **overrides: object) -> ScoutConfig:
        """Create a config from a named profile with optional overrides."""
        if profile not in _PROFILES:
            raise ValueError(f"Unknown profile: {profile!r}. Choose from: {', '.join(_PROFILES)}")
        base: dict[str, object] = dict(_PROFILES[profile])
        base.update(overrides)
        return cls(**base)  # type: ignore[arg-type]
