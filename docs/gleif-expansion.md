# GLEIF Corporate Hierarchy

Scout can expand queries to a company's full corporate family using [GLEIF](https://www.gleif.org/) (Global Legal Entity Identifier Foundation) data. When you search for "Berkshire Hathaway", Scout finds domains for subsidiaries like Gen Re, PacifiCorp, and AltaLink — not just domains whose cert org fuzzy-matches the parent name.

## Setup

Install the GLEIF optional dependency:

```bash
pip install domain-scout-ct[gleif]
```

Download the GLEIF golden copy (~450MB download, ~650MB database):

```bash
domain-scout gleif-ingest
```

This downloads entity and relationship data from [gleif.org](https://goldencopy.gleif.org/) and builds an indexed DuckDB file at `~/.local/share/domain-scout/gleif.duckdb`.

## Usage

### CLI

```bash
domain-scout scout --name "Berkshire Hathaway" \
  --gleif-db ~/.local/share/domain-scout/gleif.duckdb
```

Or set the environment variable:

```bash
export DOMAIN_SCOUT_GLEIF_DB=~/.local/share/domain-scout/gleif.duckdb
domain-scout scout --name "Berkshire Hathaway"
```

### Python API

```python
from domain_scout.config import ScoutConfig
from domain_scout.scout import Scout

config = ScoutConfig(gleif_db_path="/path/to/gleif.duckdb")
scout = Scout(config=config)
result = await scout.discover_async(EntityInput(company_name="Berkshire Hathaway"))
```

## How it works

1. **Entity lookup** — Scout searches GLEIF for the queried company name using 4-stage matching (exact → case-insensitive → prefix → fuzzy). Prefers entities with subsidiaries to avoid matching empty shell entities.

2. **Corporate tree expansion** — Traverses parent/subsidiary/sibling relationships, including multi-hop paths via `IS_ULTIMATELY_CONSOLIDATED_BY`. For Berkshire Hathaway, this finds ~124 subsidiaries.

3. **Brand filtering** — Filters subsidiaries to those with distinct brand names (removes shell companies, holding entities, and names that overlap with the parent). Typically reduces 124 raw subsidiaries to ~30 searchable brands.

4. **CT search per subsidiary** — Each filtered subsidiary name triggers a certificate transparency search. Found domains are tagged with `ct_gleif_subsidiary` in the evidence chain.

5. **Sibling dedup** — Domains whose cert org matches a sibling entity (not the queried entity) receive a confidence penalty to prevent cross-attribution.

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `gleif_db_path` | None | Path to GLEIF DuckDB file |
| `gleif_max_subsidiaries` | 5 | Max subsidiaries to search (depth limit) |

## Evidence output

Domains found via GLEIF expansion include the subsidiary name in the evidence chain:

```json
{
  "domain": "pacificorp.com",
  "confidence": 0.80,
  "sources": ["ct_gleif_subsidiary"],
  "evidence": [
    {
      "source_type": "ct_gleif_subsidiary",
      "description": "Cert org 'PacifiCorp' matches subsidiary 'PacifiCorp' (score=1.00)",
      "signal_type": "cert_org_subsidiary",
      "signal_weight": 0.50
    }
  ]
}
```

## Limitations

- **GLEIF coverage is incomplete.** Many subsidiaries (especially acquired companies) don't have LEI registrations linking them to their parent. For example, GEICO has no LEI under Berkshire Hathaway.
- **Regional offices** with the parent's name (e.g., "Palo Alto Networks (UK) Limited") are filtered out — the parent CT search already covers them.
- **GLEIF data refreshes daily** at gleif.org. Re-run `domain-scout gleif-ingest` periodically to stay current.
