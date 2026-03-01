# RDAP ccTLD Support

Not all country-code TLDs (ccTLDs) participate in the RDAP protocol.
domain-scout skips RDAP lookups for TLDs that are known to return 404 from
the rdap.org bootstrap service, avoiding unnecessary HTTP round-trips and
noisy error logs.

Source: [IANA RDAP Bootstrap Registry](https://data.iana.org/rdap/dns.json)

## ccTLDs with RDAP support (in IANA bootstrap)

These TLDs are served by rdap.org and return structured registration data:

`.ar` `.au` `.br` `.ca` `.cz` `.fi` `.fr` `.id` `.in` `.nl` `.no` `.pl`
`.sg` `.si` `.th` `.tw` `.uk`

Generic TLDs (`.com`, `.net`, `.org`, etc.) are also fully supported.

## ccTLDs with RDAP but no useful registrant data (GDPR)

Some European registries participate in the RDAP bootstrap but redact all
registrant fields due to GDPR. The response is valid JSON but contains no
actionable org/name/country data:

- `.de` (DENIC)
- `.ch` (SWITCH)

These are included in the skip list because the lookup cost produces no
corroboration value.

## ccTLDs without RDAP (skip list)

These TLDs are **not** in the IANA RDAP bootstrap registry. Requests to
rdap.org return HTTP 404. domain-scout skips them automatically:

```
.ae .at .be .bg .ch .cl .cn .co .de .dk .edu .ee .es
.hk .hr .hu .ie .il .io .it .jp .kr .lt .lu .lv
.mx .my .nz .pe .ro .ru .se .sk .tr .us .za
```

Note: `.edu` is not a ccTLD but is also absent from the RDAP bootstrap.

## Why no WHOIS fallback?

WHOIS (port 43) is an older protocol that most registries still support.
However, implementing WHOIS fallback for skipped TLDs was not pursued for
several reasons:

1. **Per-registry parsing**: Every WHOIS server returns free-form text in a
   different format. There is no standard schema. Supporting N registries
   means N parsers.
2. **GDPR redaction**: European registries (and many others) redact registrant
   fields in WHOIS responses, returning "REDACTED FOR PRIVACY" or equivalent.
   The data is not there to parse.
3. **Rate limiting**: Many WHOIS servers aggressively rate-limit automated
   queries (e.g., DENIC limits to 1 query/minute).
4. **Marginal ROI**: RDAP corroboration is one signal among several
   (CT logs, DNS, similarity scoring). Missing RDAP data for a subset of
   TLDs has minimal impact on overall confidence scoring.
