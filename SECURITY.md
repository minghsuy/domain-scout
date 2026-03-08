# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.7.x   | Yes       |
| < 0.7   | No        |

## Reporting a vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public issue
2. Use [GitHub Security Advisories](https://github.com/minghsuy/domain-scout/security/advisories/new) to report privately
3. Include steps to reproduce and potential impact

You should receive a response within 48 hours. Security fixes will be released as patch versions.

## Scope

domain-scout queries external services (crt.sh, RDAP, DNS, Shodan). Security concerns include:

- **Input validation** — company names and domains are used in database queries and DNS lookups
- **API server** — the REST API (`domain-scout serve`) accepts untrusted input
- **Path traversal** — file paths in local mode and cache configuration
- **Dependency vulnerabilities** — transitive dependencies in httpx, psycopg2, etc.
