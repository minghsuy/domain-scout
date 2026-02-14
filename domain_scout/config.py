"""Configuration constants and defaults."""

from __future__ import annotations

from dataclasses import dataclass, field


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
