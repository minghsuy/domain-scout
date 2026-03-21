# API Reference

domain-scout can be used as a Python library for programmatic domain discovery.

## Basic usage

```python
from domain_scout import Scout

result = Scout().discover(
    company_name="Palo Alto Networks",
    seed_domain="paloaltonetworks.com",
)

for domain in result.domains:
    print(f"{domain.domain:40s}  {domain.confidence:.2f}  {domain.sources}")
```

## Async usage

```python
import asyncio
from domain_scout import Scout, EntityInput

async def main():
    scout = Scout()
    result = await scout.discover_async(EntityInput(
        company_name="Palo Alto Networks",
        seed_domain=["paloaltonetworks.com"],
    ))
    return result

result = asyncio.run(main())
```

## Configuration

```python
from domain_scout import Scout
from domain_scout.config import ScoutConfig

config = ScoutConfig(
    total_timeout=180,          # seconds
    deep_mode=True,             # enable GeoDNS
    discovery_mode="fingerprint",  # "default" or "fingerprint"
    fp_candidate_limit=200,     # max candidates to fingerprint-verify
    dns_timeout=5.0,            # per-query DNS timeout
    org_match_threshold=0.65,   # fuzzy match threshold
    inclusion_threshold=0.6,    # minimum confidence to include
    geodns_concurrency=3,       # concurrent GeoDNS requests
    geodns_delay=0.5,           # delay between GeoDNS requests
)

scout = Scout(config=config)
result = scout.discover(company_name="Acme Corp", seed_domain="acme.com")
```

## Discovery profiles

Profiles provide preset threshold configurations for different use cases:

```python
from domain_scout.config import ScoutConfig

config = ScoutConfig.from_profile("broad")    # lower thresholds, includes non-resolving
config = ScoutConfig.from_profile("balanced")  # defaults
config = ScoutConfig.from_profile("strict")    # higher thresholds

# Profiles accept overrides
config = ScoutConfig.from_profile("broad", total_timeout=200)
```

Or via CLI: `domain-scout --name "Acme" --seed acme.com --profile strict`

## Response models

### ScoutResult

```python
class ScoutResult(BaseModel):
    entity: EntityInput                                  # the input
    domains: list[DiscoveredDomain]                      # discovered domains, sorted by confidence
    seed_domain_assessment: dict[str, str]               # seed -> "confirmed" | "suspicious" | "invalid" | "timeout"
    seed_cross_verification: dict[str, list[str]]        # seed -> list of co-hosted seeds
    run_metadata: RunMetadata                             # audit trail and reproducibility metadata
```

### DiscoveredDomain

```python
class DiscoveredDomain(BaseModel):
    domain: str                          # e.g. "samsclub.com"
    confidence: float                    # 0.0 to 1.0
    sources: list[str]                   # e.g. ["ct_org_match", "ct_san_expansion:walmart.com"]
    evidence: list[EvidenceRecord]       # structured attribution evidence
    cert_org_names: list[str]            # organization names from certificates
    first_seen: datetime | None          # earliest cert notBefore
    last_seen: datetime | None           # latest cert notAfter
    resolves: bool                       # DNS resolution status
    is_seed: bool                        # True if this is a seed domain
    seed_sources: list[str]              # which seeds contributed to discovering this domain
```

### EvidenceRecord

```python
class EvidenceRecord(BaseModel):
    source_type: str                     # e.g. "ct_org_match", "ct_san_expansion", "dns_guess"
    description: str                     # human-readable explanation
    seed_domain: str | None = None       # which seed produced this evidence
    cert_id: int | None = None           # crt.sh certificate ID (links to https://crt.sh/?id=N)
    cert_org: str | None = None          # O= field from the certificate
    similarity_score: float | None = None  # org-name similarity 0.0-1.0
```

### RunMetadata

```python
class RunMetadata(BaseModel):
    schema_version: str = "1.0"          # output schema version
    tool_version: str                    # domain-scout package version
    timestamp: datetime                  # UTC timestamp of the run
    elapsed_seconds: float               # wall-clock duration
    domains_found: int                   # number of domains in output
    timed_out: bool = False              # whether any phase timed out
    seed_count: int = 0                  # number of seed domains used
    errors: list[str]                    # warnings and errors encountered
    config: dict[str, object]            # snapshot of ScoutConfig used
```

### EntityInput

```python
class EntityInput(BaseModel):
    company_name: str                        # required
    location: str | None = None              # optional
    seed_domain: list[str] = []              # optional, repeatable
    industry: str | None = None              # optional
```

## Delta reporting

Compare two scan results to see what changed:

```python
from domain_scout import compute_delta, Scout

baseline = Scout().discover(company_name="Acme Corp", seed_domain="acme.com")
# ... time passes ...
current = Scout().discover(company_name="Acme Corp", seed_domain="acme.com")

report = compute_delta(baseline, current)
print(f"Added: {report.summary.added}, Removed: {report.summary.removed}")
for d in report.added:
    print(f"  + {d.domain}")
for d in report.removed:
    print(f"  - {d.domain}")
for c in report.changed:
    print(f"  ~ {c.domain}: {[ch.field for ch in c.changes]}")
```

Or via CLI:

```bash
domain-scout diff baseline.json current.json            # table output
domain-scout diff baseline.json current.json -o json    # JSON output
```

### DeltaReport

```python
class DeltaReport(BaseModel):
    added: list[DiscoveredDomain]        # domains in current but not baseline
    removed: list[DiscoveredDomain]      # domains in baseline but not current
    changed: list[ChangedDomain]         # domains in both with meaningful differences
    summary: DeltaSummary                # aggregate counts
    warnings: list[DeltaWarning]         # context warnings (different seeds, config, etc.)
    baseline_metadata: RunMetadata       # metadata from the baseline scan
    current_metadata: RunMetadata        # metadata from the current scan
```

### ChangedDomain

```python
class ChangedDomain(BaseModel):
    domain: str                          # e.g. "samsclub.com"
    changes: list[DomainChange]          # field-level changes
    baseline_confidence: float           # confidence in baseline
    current_confidence: float            # confidence in current
```

### DomainChange

```python
class DomainChange(BaseModel):
    field: str                           # "confidence", "resolves", "sources", or "rdap_org"
    old: float | bool | str | list[str] | None
    new: float | bool | str | list[str] | None
```

### DeltaSummary

```python
class DeltaSummary(BaseModel):
    added: int
    removed: int
    changed: int
    unchanged: int
    baseline_total: int
    current_total: int
```

### DeltaWarning

```python
class DeltaWarning(BaseModel):
    code: str                            # e.g. "seeds_changed", "config_changed"
    message: str                         # human-readable explanation
```

## REST API

Start the server:

```bash
domain-scout serve --port 8080
domain-scout serve --port 8080 --api-key YOUR_KEY  # require authentication
```

### Server environment variables

Configure server-wide defaults so clients don't need to pass paths per request:

| Variable | Description | Default |
|----------|-------------|---------|
| `DOMAIN_SCOUT_WAREHOUSE_PATH` | Path to parquet warehouse directory | None |
| `DOMAIN_SCOUT_SUBSIDIARIES_PATH` | Path to subsidiaries CSV file | None |
| `DOMAIN_SCOUT_LOCAL_MODE` | `disabled`, `local_only`, or `local_first` | `disabled` (auto-enables `local_first` if warehouse path is set) |
| `DOMAIN_SCOUT_API_KEY` | Require this key on authenticated endpoints | None |
| `DOMAIN_SCOUT_CACHE` | Enable DuckDB cache (`true`/`false`) | `true` |
| `DOMAIN_SCOUT_CACHE_DIR` | DuckDB cache directory | System default |
| `DOMAIN_SCOUT_MAX_CONCURRENT` | Max concurrent scans | `3` |

Example deployment with warehouse:

```bash
export DOMAIN_SCOUT_WAREHOUSE_PATH=/opt/ct-warehouse
export DOMAIN_SCOUT_API_KEY=secret
domain-scout serve --port 8080
# Server auto-enables local_first mode — clients just POST to /scan
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/scan` | Run a domain discovery scan |
| `POST` | `/diff` | Compare two scan results |
| `GET` | `/health` | Health check (returns version + status) |
| `GET` | `/ready` | Readiness probe (checks crt.sh connectivity) |
| `GET` | `/cache/stats` | Cache statistics |
| `POST` | `/cache/clear` | Clear all cached entries |
| `GET` | `/metrics` | Prometheus metrics |

Authenticated endpoints (`/scan`, `/diff`, `/cache/*`) require `X-API-Key` header when `--api-key` is set.

### POST /scan

```json
{
  "entity": {
    "company_name": "Shelter Insurance",
    "seed_domain": ["shelterinsurance.com"]
  },
  "profile": "balanced",
  "timeout": 120,
  "deep": false,
  "local_mode": null,
  "warehouse_path": null,
  "subsidiaries_path": null
}
```

Only `entity.company_name` is required. All other fields are optional:

| Field | Type | Description |
|-------|------|-------------|
| `profile` | `broad` \| `balanced` \| `strict` | Threshold preset |
| `timeout` | `5-300` | Override total timeout (seconds) |
| `deep` | `bool` | Enable GeoDNS deep mode |
| `local_mode` | `disabled` \| `local_only` \| `local_first` \| `null` | Override server default |
| `warehouse_path` | `string` \| `null` | Override server default warehouse path |
| `subsidiaries_path` | `string` \| `null` | Override server default subsidiaries path |

When `local_mode`, `warehouse_path`, or `subsidiaries_path` are `null` (or omitted), the server's environment variable defaults are used.

Returns a `ScoutResult` JSON object.

## JSON output

Use `model_dump_json()` for serialization:

```python
result = scout.discover(company_name="Acme Corp")
print(result.model_dump_json(indent=2))
```
