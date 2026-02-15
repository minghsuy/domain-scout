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
        seed_domain="paloaltonetworks.com",
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

## Response models

### ScoutResult

```python
class ScoutResult(BaseModel):
    entity: EntityInput              # the input
    domains: list[DiscoveredDomain]  # discovered domains, sorted by confidence
    seed_domain_assessment: str      # "confirmed", "suspicious", "invalid", or "timeout"
    search_metadata: dict            # elapsed_seconds, domains_found, errors, timed_out
```

### DiscoveredDomain

```python
class DiscoveredDomain(BaseModel):
    domain: str                 # e.g. "samsclub.com"
    confidence: float           # 0.0 to 1.0
    sources: list[str]          # e.g. ["ct_org_match", "ct_san_expansion"]
    evidence: list[str]         # human-readable evidence strings
    cert_org_names: list[str]   # organization names from certificates
    first_seen: Any             # earliest cert notBefore
    last_seen: Any              # latest cert notAfter
    resolves: bool              # DNS resolution status
    is_seed: bool               # True if this is the seed domain
```

### EntityInput

```python
class EntityInput(BaseModel):
    company_name: str               # required
    location: str | None = None     # optional
    seed_domain: str | None = None  # optional
    industry: str | None = None     # optional
```

## JSON output

Use `model_dump_json()` for serialization:

```python
result = scout.discover(company_name="Acme Corp")
print(result.model_dump_json(indent=2))
```
