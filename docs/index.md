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

# Deep mode for global resolution
domain-scout --name "Walmart" --seed "walmart.com" --deep
```

## Documentation

- [Getting Started](getting-started.md) — Installation, first run, CLI options
- [How It Works](architecture.md) — Pipeline architecture, data sources, scoring
- [Deep Mode](deep-mode.md) — GeoDNS global resolution for regional domains
- [Examples](examples.md) — Real-world results for Walmart, Generali, and more
- [API Reference](api.md) — Using domain-scout as a Python library

## License

MIT
