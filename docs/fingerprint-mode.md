# Fingerprint Mode

DNS fingerprint expansion mode (`--mode fingerprint`) discovers domains by shared DNS infrastructure rather than certificate organization names. Designed for companies using DV (Domain Validation) certificates where CT org search returns zero results.

## When to use

Standard discovery relies on the Organization (O=) field in TLS certificates. Companies using DV certificates have no org name in their certs, making the primary CT org search blind. This is common among:

- Insurtech companies (Lemonade, Hippo, Next Insurance)
- Startups and digital-first businesses
- Companies using Let's Encrypt, Cloudflare, or other DV-only CAs

If `domain-scout` only finds the seed domain in default mode, try fingerprint mode.

## How it works

```
Seed Domain
    |
    v
Extract DNS Fingerprint (MX, NS, TXT)
    |
    v
Existing strategies still run:
  - Strategy B: Seed SAN expansion (works with DV certs)
  - Strategy C: Domain guessing
  - Strategy D: Subsidiary expansion (if configured)
  - Strategy A: CT org search (SKIPPED - useless for DV certs)
    |
    v
For each candidate domain:
  Extract its DNS fingerprint
  Compare against seed fingerprint
  Add evidence for matching signals
    |
    v
Score using existing corroboration tiers
```

## Fingerprint signals

### MX tenant ID (strong signal)

Enterprise email providers assign per-customer MX hostnames. If two domains share the same MX tenant, they almost certainly belong to the same organization.

Supported providers:

| Provider | MX pattern | Example |
|----------|-----------|---------|
| Proofpoint | `mxa-{id}.gslb.pphosted.com` | `mxa-002d0c01.gslb.pphosted.com` |
| Microsoft 365 | `{tenant}.mail.protection.outlook.com` | `contoso-com.mail.protection.outlook.com` |
| Barracuda | `{tenant}.ess.barracudanetworks.com` | `acmecorp.ess.barracudanetworks.com` |
| IronPort | `{tenant}.iphmx.com` | `acmecorp.iphmx.com` |
| FireEye/Trellix | `{tenant}.fireeyecloud.com` | `acmecorp.fireeyecloud.com` |

Providers intentionally excluded:

- **Google Workspace** — all customers share the same MX (`aspmx.l.google.com`)
- **Mimecast** — inbound MX is shared infrastructure (`us-smtp-inbound-1.mimecast.com`), not per-tenant

### NS zone (moderate signal, filtered)

Shared nameserver zones can indicate common DNS management. However, large DNS providers (Cloudflare, AWS Route53, Azure DNS) host millions of unrelated domains, so these are filtered out.

Blocked NS zones: `cloudflare.com`, `awsdns-*.com`, `azure-dns.com`, `google.com`, `domaincontrol.com` (GoDaddy), `nsone.net`, and others.

Only custom/private NS zones produce matching signals.

### SPF includes (moderate signal, filtered)

Shared SPF include records can indicate common email infrastructure. Common SaaS providers are filtered out.

Blocked SPF includes: `spf.protection.outlook.com`, `_spf.google.com`, `sendgrid.net`, `amazonses.com`, `mailgun.org`, `zendesk.com`, and others.

## Scoring

Fingerprint signals map to existing corroboration tiers:

- **MX tenant match** is treated as equivalent to `rdap_registrant_match` — a strong org-level signal that triggers corroboration boosts
- **NS zone + SPF matches** contribute to multi-source count but are not strong enough standalone

## Usage

```bash
# Basic fingerprint mode
domain-scout --name "Shelter Insurance" --seed shelterinsurance.com --mode fingerprint

# JSON output for programmatic use
domain-scout --name "Company" --seed company.com --mode fingerprint -o json
```

Fingerprint mode automatically implies `--deep` (GeoDNS) and sets the timeout to at least 180 seconds.

## Configuration

| Config field | Default | Description |
|-------------|---------|-------------|
| `discovery_mode` | `"default"` | Set to `"fingerprint"` to enable |
| `fp_candidate_limit` | `200` | Max candidates to fingerprint-verify |

```python
from domain_scout.config import ScoutConfig
from domain_scout.scout import Scout

config = ScoutConfig(discovery_mode="fingerprint", fp_candidate_limit=100)
scout = Scout(config=config)
result = scout.discover(company_name="Shelter Insurance", seed_domain="shelterinsurance.com")
```

## Example: Shelter Insurance

Shelter Insurance uses DV certificates and Proofpoint for email. Default mode finds 3 domains; fingerprint mode finds 5:

```
=== FINGERPRINT MODE ===
  amshieldinsurance.com                      0.95  [ct_org_match, fp:mx_tenant, fp:ns_zone, shared_infra]
    ^ Shares proofpoint MX tenant 'proofpoint:002d0c01' with seed
  shelterinsurance.com                       0.90  [ct_org_match, ct_seed_subdomain, dns_guess]
  sayinsurance.com                           0.90  [ct_org_match, fp:mx_tenant]
    ^ Shares proofpoint MX tenant 'proofpoint:002d0c01' with seed
  shelterre.com                              0.90  [ct_org_match, fp:ns_zone, shared_infra]
  cloudflaressl.com                          0.80  [ct_san_expansion]
```

All findings verified: AmShield is Shelter's commercial subsidiary (est. 2014), Say Insurance was a former Shelter brand, and Shelter Re is their reinsurance arm (est. 1986).

## Limitations

- Requires at least one seed domain with a parseable MX tenant for best results
- Companies using Google Workspace or other shared-MX providers won't benefit from MX tenant matching (NS zone and SPF signals may still help)
- Candidate domains must already be discovered by other strategies (SAN expansion, domain guessing) — fingerprint mode verifies and boosts them, it doesn't generate new candidates from scratch
- Shodan reverse DNS for candidate generation is planned but not yet implemented (see [#110](https://github.com/minghsuy/domain-scout/issues/110))
