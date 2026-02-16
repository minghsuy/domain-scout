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

## JSON output

Use `model_dump_json()` for serialization:

```python
result = scout.discover(company_name="Acme Corp")
print(result.model_dump_json(indent=2))
```
