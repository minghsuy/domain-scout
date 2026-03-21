# domain-scout

**Discover internet domains associated with a business entity.**

domain-scout uses Certificate Transparency logs, RDAP, DNS, and Shodan GeoDNS to map out all the domains an organization owns — even regional subsidiaries, acquired companies, and alternate TLDs that don't show up in standard lookups.

## Why

Organizations often own dozens or hundreds of domains across different TLDs and registrars. Finding them all is hard:

- **Acquisitions** bring domains that never get consolidated (e.g., Walmart owns `asda.com`, `bodegaaurrera.com.mx`, `game.co.za`)
- **Regional operations** use country-code TLDs that don't resolve from US DNS (e.g., `.cn`, `.mx`, `.co.za`)
- **Brand variations** and **legacy domains** get forgotten but stay on certificates
- **Seed domains** from third parties are often wrong, misspelled, or point to a parent company

domain-scout handles all of these by cross-referencing multiple data sources and scoring each domain by confidence.

## Quick start

```bash
# Install
uv sync

# Run
domain-scout --name "Walmart" --seed "walmart.com"

# Multiple seeds for cross-verification
domain-scout --name "Walmart" --seed walmart.com --seed samsclub.com

# Discovery profiles (broad/balanced/strict)
domain-scout --name "Walmart" --seed walmart.com --profile strict

# Deep mode for global resolution
domain-scout --name "Walmart" --seed "walmart.com" --deep

# Fingerprint mode for DV-cert companies (no org in certs)
domain-scout --name "Shelter Insurance" --seed shelterinsurance.com --mode fingerprint
```

## Documentation

- [Getting Started](getting-started.md) — Installation, first run, CLI options
- [How It Works](architecture.md) — Pipeline architecture, data sources, scoring
- [Multi-Seed Discovery](multi-seed.md) — Cross-verification with multiple seed domains
- [Deep Mode](deep-mode.md) — GeoDNS global resolution for regional domains
- [Fingerprint Mode](fingerprint-mode.md) — DNS fingerprinting for DV-cert companies
- [Examples](examples.md) — Real-world results for Walmart, Generali, Shelter Insurance, and more
- [API Reference](api.md) — Using domain-scout as a Python library

## License

MIT
