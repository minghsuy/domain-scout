# Examples

Real-world results demonstrating domain-scout's capabilities.

## Walmart

A multinational retailer with subsidiaries across Mexico, South Africa, UK, India, and China.

```bash
domain-scout --name "Walmart" --seed "walmart.com" --deep
```

**17 domains found** including:

| Domain | Confidence | Notes |
|--------|-----------|-------|
| `walmart.com` | 1.00 | Seed domain |
| `samsclub.com` | 1.00 | US subsidiary |
| `walmart.ca` | 1.00 | Canada |
| `walmart.com.mx` | 1.00 | Mexico |
| `bodegaaurrera.com.mx` | 1.00 | Mexico (Bodega Aurrera brand) |
| `sams.com.mx` | 1.00 | Mexico (Sam's Club) |
| `asda.com` | 1.00 | UK (ASDA subsidiary) |
| `game.co.za` | 1.00 | South Africa |
| `makro.co.za` | 1.00 | South Africa |
| `bestprice.in` | 1.00 | India |
| `walmartlabs.com` | 1.00 | Technology division |

All discovered through Certificate Transparency SAN co-occurrence + organization name matching.

## Generali

An Italian multinational financial services company operating across Europe, Middle East, and Asia.

```bash
domain-scout --name "Generali" --seed "generali.it"
```

**43 domains found** across multiple countries:

| Domain | Confidence | Country |
|--------|-----------|---------|
| `generali.com` | 1.00 | Global |
| `generali.it` | 0.95 | Italy (seed) |
| `generali.de` | 0.90 | Germany |
| `generali.fr` | 0.90 | France |
| `generali.ch` | 0.90 | Switzerland |
| `generali.hu` | 0.90 | Hungary |
| `generali.sk` | 0.90 | Slovakia |
| `genertel.it` | 0.95 | Italy (direct brand) |
| `cattolica.it` | 0.95 | Italy (acquired company) |
| `alleanza.it` | 0.95 | Italy (subsidiary) |
| `futuregenerali.in` | 0.90 | India (joint venture) |
| `generali-uae.com` | 0.95 | UAE |
| `dialog-versicherung.de` | 0.90 | Germany (Dialog brand) |

Demonstrates strong ccTLD coverage through CT org matching across 15+ countries.

## Seed domain matters

The choice of seed domain affects which domains are discovered through SAN expansion (Strategy B). Comparing `generali.it` vs `generali.com` as seed:

| | `--seed generali.it` | `--seed generali.com` |
|---|---|---|
| **Total domains** | 59 | 57 |
| **In common** | 43 | 43 |
| **Unique to this seed** | 16 | 14 |

The `.it` seed finds 16 additional Czech/Italian/Slovak domains (e.g., `ceskapojistovna.cz`, `vubgenerali.sk`, `generali.at`) because those appear as SANs on certificates covering `generali.it`.

The `.com` seed finds 14 additional investment-related domains (e.g., `generali-invest.de`, `generali-investments.lu`, `generali.co.uk`) from certificates covering `generali.com`.

**Takeaway:** For multinational organizations, use multiple seeds to get the full picture:

```bash
domain-scout --name "Generali" --seed generali.it --seed generali.com
```

This finds the union of both seed sets (73+ unique domains) with cross-verified overlap domains scored at 0.90+ confidence.

## Multi-seed cross-verification

When using multiple seeds, domains discovered independently from 2+ seeds receive a `cross_seed_verified` source with a 0.90 base score:

```bash
domain-scout --name "Walmart" --seed walmart.com --seed samsclub.com
```

If both seeds independently discover `walmartlabs.com` through separate CT searches, that convergence is a strong ownership signal. The seeds themselves may also share certificates, proving common ownership.

## How to read the output

- **Confidence** — 0.0 to 1.0 score based on multiple signals (see [Architecture](architecture.md))
- **Resolves** — whether the domain resolves in DNS (`yes`/`no`)
- **Sources** — which discovery strategies found this domain:
  - `ct_org_match` — Certificate org name matches target
  - `ct_san_expansion:{seed}` — Found on same cert as a seed domain
  - `ct_seed_subdomain:{seed}` — Subdomain of a seed
  - `ct_seed_related:{seed}` — Found in CT search for a seed
  - `cross_seed_verified` — Independently found from 2+ seeds (multi-seed only)
  - `dns_guess` — Guessed from company name, resolves
  - `shared_infra` — Shares nameservers or IP range with seed
  - `geodns` — Resolved via Shodan GeoDNS (deep mode)
- **Evidence** (JSON output) — each domain carries structured `EvidenceRecord` entries with `source_type`, `cert_id` (links to `https://crt.sh/?id=N`), `cert_org`, `similarity_score`, and `seed_domain`. See [API Reference](api.md#evidencerecord) for the full schema.
- **Run metadata** (JSON output) — the top-level `run_metadata` captures `tool_version`, `timestamp`, `elapsed_seconds`, and a full `config` snapshot for audit reproducibility.
