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

An Italian multinational insurance company operating across Europe, Middle East, and Asia.

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
| `genertel.it` | 0.95 | Italy (direct insurance brand) |
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

**Takeaway:** For multinational organizations, the seed domain closest to the company's primary operations yields the best SAN expansion coverage. When in doubt, try multiple runs with different seeds.

## How to read the output

- **Confidence** — 0.0 to 1.0 score based on multiple signals (see [Architecture](architecture.md))
- **Resolves** — whether the domain resolves in DNS (`yes`/`no`)
- **Sources** — which discovery strategies found this domain:
  - `ct_org_match` — Certificate org name matches target
  - `ct_san_expansion` — Found on same cert as seed domain
  - `ct_seed_subdomain` — Subdomain of seed
  - `dns_guess` — Guessed from company name, resolves
  - `shared_infra` — Shares nameservers or IP range with seed
  - `geodns` — Resolved via Shodan GeoDNS (deep mode)
