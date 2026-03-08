"""Main orchestrator: ties together CT log search, RDAP, DNS, and entity matching."""

from __future__ import annotations

import asyncio
import csv
import re
import time
from datetime import UTC, datetime
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from domain_scout._metrics import (
    DOMAINS_FOUND,
    SCAN_DURATION_SECONDS,
    SCANS_TOTAL,
    SOURCE_ERRORS_TOTAL,
    inc,
    observe,
)

if TYPE_CHECKING:
    from domain_scout.cache import CTSource, DuckDBCache, RDAPSource

from domain_scout.config import ScoutConfig
from domain_scout.matching.entity_match import (
    domain_from_company_name,
    normalize_org_name,
    org_name_similarity,
)
from domain_scout.models import (
    DiscoveredDomain,
    EntityInput,
    EvidenceRecord,
    RunMetadata,
    ScoutResult,
)
from domain_scout.sources.ct_logs import CTLogSource, extract_base_domain, is_valid_domain
from domain_scout.sources.dns_utils import DNSChecker
from domain_scout.sources.rdap import RDAPLookup

log = structlog.get_logger()


def load_subsidiary_map(csv_path: str) -> dict[str, list[str]]:
    """Load parent→subsidiary mappings from an EDGAR Exhibit 21 CSV.

    Returns a dict keyed by normalized parent_name → list of filtered subsidiary
    names (distinct brands only, ranked by brand distinctness).
    """

    raw: dict[str, list[str]] = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parent = row["parent_name"]
            sub = row["subsidiary_name"]
            key = normalize_org_name(parent)
            raw.setdefault(key, []).append(sub)

    # Filter each parent's subsidiaries to distinct brands
    result: dict[str, list[str]] = {}
    for parent_key, subs in raw.items():
        filtered = _filter_subsidiaries(parent_key, subs)
        if filtered:
            result[parent_key] = filtered
    return result


_SHELL_WORDS = frozenset(
    {
        "llc",
        "lp",
        "holdings",
        "holding",
        "investments",
        "capital",
        "management",
        "partners",
        "fund",
        "trust",
        "re",
    }
)


def _filter_subsidiaries(parent_normalized: str, subs: list[str]) -> list[str]:
    """Filter subsidiaries to those likely to have distinct CT certs."""
    parent_words = set(parent_normalized.split())
    seen: set[str] = set()
    result: list[str] = []

    for sub in subs:
        norm = normalize_org_name(sub)
        if norm in seen or not norm:
            continue
        seen.add(norm)

        # Skip garbage: numeric-only, too short after normalization
        if len(norm) < 3 or norm.replace(" ", "").isdigit():
            continue

        sub_words = set(norm.split())
        # Skip if name shares significant words with parent (already found by parent org search)
        significant_overlap = (parent_words & sub_words) - _SHELL_WORDS
        if significant_overlap:
            continue
        # Skip pure legal shells (all words are generic)
        if sub_words <= _SHELL_WORDS:
            continue
        # Skip names where all words are <=3 chars (acronym soup like "17A LLC", "RLC LLC")
        if all(len(w) <= 3 for w in sub_words - _SHELL_WORDS):
            continue
        result.append(sub)

    result.sort(key=_brand_sort_key)
    return result


# Words too generic to count as "brand" signal in subsidiary names.
_GENERIC_WORDS = _SHELL_WORDS | frozenset(
    {
        "services",
        "solutions",
        "international",
        "global",
        "enterprises",
        "financial",
        "properties",
        "advisors",
        "consulting",
        "associates",
        "ventures",
        "realty",
        "hotel",
        "hotels",
    }
)

# Suffixes to strip when detecting all-caps acronym cores.
_SUFFIX_RE = re.compile(
    r"\b(LLC|Inc|Incorporated|Ltd|Limited|Corp|Corporation|Company|LP|"
    r"GmbH|SA|SE|NV|plc|L\.P\.|L\.L\.C\.)\b",
    re.IGNORECASE,
)


def _brand_sort_key(name: str) -> tuple[int, int]:
    """Sort key ranking subsidiaries by brand distinctness.

    Returns ``(bucket, name_length)`` — lower is better.

    * **Bucket 0** — focused brand: 1-3 non-generic words, no numbers, not
      an all-caps acronym.  e.g. "Texaco Inc.", "LinkedIn Corporation".
    * **Bucket 1** — decent: has brand words but verbose (>3 words).
      e.g. "Cabinda Gulf Oil Company Limited".
    * **Bucket 2** — weak: contains numbers, all-caps acronym, or no real
      brand words.  e.g. "Westin 200, Inc.", "FTNV LLC", "TSQ2, LLC".
    """
    norm = normalize_org_name(name)
    words = norm.split()
    brand_words = [w for w in words if len(w) >= 4 and w not in _GENERIC_WORDS]

    has_numbers = bool(re.search(r"\d", name))

    # Detect all-caps acronym core (e.g. "FTNV LLC" → core "FTNV")
    core = _SUFFIX_RE.sub("", name)
    core = re.sub(r"[^a-zA-Z]", "", core)
    is_acronym = bool(core) and core == core.upper() and len(core) <= 8

    if has_numbers or is_acronym:
        bucket = 2
    elif brand_words and len(words) <= 3:
        bucket = 0
    elif brand_words:
        bucket = 1
    else:
        bucket = 2

    return (bucket, len(name))


class Scout:
    """Discover internet domains associated with a business entity."""

    def __init__(
        self,
        config: ScoutConfig | None = None,
        cache: DuckDBCache | None = None,
    ) -> None:
        self.config = config or ScoutConfig()
        rdap_inner = RDAPLookup(self.config)

        if self.config.local_mode == "local_only":
            from domain_scout.sources.local_parquet import LocalParquetSource

            self._ct: CTSource | CTLogSource = LocalParquetSource(self.config)
        elif self.config.local_mode == "local_first":
            from domain_scout.sources.local_parquet import HybridCTSource, LocalParquetSource

            local = LocalParquetSource(self.config)
            remote = CTLogSource(self.config)
            self._ct = HybridCTSource(local, remote)
        else:
            ct_inner = CTLogSource(self.config)
            if cache is not None:
                from domain_scout.cache import CachedCTLogSource

                self._ct = CachedCTLogSource(ct_inner, cache)
            else:
                self._ct = ct_inner

        if cache is not None:
            from domain_scout.cache import CachedRDAPLookup

            self._rdap: RDAPSource | RDAPLookup = CachedRDAPLookup(rdap_inner, cache)
        else:
            self._rdap = rdap_inner
        self._dns = DNSChecker(self.config)
        self._subsidiaries: dict[str, list[str]] = {}
        if self.config.subsidiaries_path:
            try:
                self._subsidiaries = load_subsidiary_map(self.config.subsidiaries_path)
                log.info("scout.subsidiaries_loaded", parents=len(self._subsidiaries))
            except Exception:
                log.warning("scout.subsidiaries_load_failed", exc_info=True)

    def _match_subsidiaries(self, company_name: str) -> list[str]:
        """Find subsidiary names for a company using the loaded EDGAR map."""
        normalized = normalize_org_name(company_name)
        # Exact match first
        if normalized in self._subsidiaries:
            return list(self._subsidiaries[normalized])
        # Fuzzy match against parent names
        best_key: str | None = None
        best_score = 0.0
        for parent_key in self._subsidiaries:
            score = org_name_similarity(normalized, parent_key)
            if score > best_score:
                best_score = score
                best_key = parent_key
                if score >= 0.95:
                    break
        if best_key and best_score >= 0.85:
            return list(self._subsidiaries[best_key])
        return []

    def discover(
        self,
        company_name: str,
        location: str | None = None,
        seed_domain: str | None | list[str] = None,
        industry: str | None = None,
    ) -> ScoutResult:
        """Synchronous entry point. Runs the async pipeline."""
        # Coerce seed_domain to list for backward compat
        if seed_domain is None:
            seeds: list[str] = []
        elif isinstance(seed_domain, str):
            seeds = [seed_domain]
        else:
            seeds = list(seed_domain)
        entity = EntityInput(
            company_name=company_name,
            location=location,
            seed_domain=seeds,
            industry=industry,
        )
        return asyncio.run(self._discover(entity))

    async def discover_async(self, entity: EntityInput) -> ScoutResult:
        """Async entry point."""
        return await self._discover(entity)

    async def _discover(self, entity: EntityInput) -> ScoutResult:
        t0 = time.monotonic()
        total_budget = self.config.total_timeout
        errors: list[str] = []
        timed_out = False
        seeds = entity.seed_domain  # list[str]

        # Accumulator: domain -> evidence dict
        domain_evidence: dict[str, _DomainAccum] = {}

        def _remaining() -> float:
            return max(0.0, total_budget - (time.monotonic() - t0))

        def _collect(results: list[Any]) -> None:
            for result in results:
                if isinstance(result, BaseException):
                    errors.append(str(result))
                    continue
                for domain, accum in result:
                    if domain in domain_evidence:
                        domain_evidence[domain].merge(accum)
                    else:
                        domain_evidence[domain] = accum

        # Phase 1: Seed validation + independent strategies run in parallel.
        # Strategies A (org search) and C (domain guess) don't need seed results.
        seed_assessments: dict[str, str] = {}
        seed_org_names: dict[str, str | None] = {}
        seed_cross_verification: dict[str, list[str]] = {}

        independent_tasks: list[asyncio.Task[list[tuple[str, _DomainAccum]]]] = []

        # Strategy A: org name search (independent of seed)
        independent_tasks.append(
            asyncio.create_task(
                self._strategy_org_search(entity.company_name, errors),
                name="org_search",
            )
        )

        # Strategy C: domain guessing (independent of seed)
        independent_tasks.append(
            asyncio.create_task(
                self._strategy_domain_guess(entity.company_name, entity.location, errors),
                name="domain_guess",
            )
        )

        # Strategy D: subsidiary expansion (if EDGAR data loaded)
        if self._subsidiaries:
            sub_names = self._match_subsidiaries(entity.company_name)
            for sub_name in sub_names[: self.config.subsidiary_max_queries]:
                independent_tasks.append(
                    asyncio.create_task(
                        self._strategy_org_search(
                            sub_name,
                            errors,
                            source_tag="ct_subsidiary_match",
                        ),
                        name=f"subsidiary:{sub_name[:30]}",
                    )
                )

        # Pre-calculate base domains for all seeds once
        seed_to_base = {s: extract_base_domain(s) for s in seeds}

        # Parallel seed validation for all seeds
        seed_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        for sd in seeds:
            seed_tasks[sd] = asyncio.create_task(
                self._validate_seed(sd, entity.company_name, seeds, errors, seed_to_base),
                name=f"seed_validation:{sd}",
            )

        # Wait for all seed validations (capped at 15s) while A/C also run
        if seed_tasks:
            try:
                seed_results = await asyncio.wait_for(
                    asyncio.gather(*seed_tasks.values(), return_exceptions=True),
                    timeout=min(15.0, _remaining()),
                )
                for sd, result in zip(seed_tasks.keys(), seed_results, strict=True):
                    if isinstance(result, BaseException):
                        errors.append(f"Seed validation failed for {sd}: {result}")
                        seed_assessments[sd] = "error"
                    else:
                        seed_assessments[sd] = result["assessment"]
                        seed_org_names[sd] = result["org_name"]
                        if result.get("co_hosted_seeds"):
                            seed_cross_verification[sd] = result["co_hosted_seeds"]
                        log.info(
                            "scout.seed_validated",
                            seed=sd,
                            assessment=result["assessment"],
                            seed_org=result["org_name"],
                            co_hosted=result.get("co_hosted_seeds", []),
                        )
            except TimeoutError:
                errors.append("Seed validation timed out")
                for sd, stask in seed_tasks.items():
                    if not stask.done():
                        stask.cancel()
                        seed_assessments.setdefault(sd, "timeout")
                    elif stask.cancelled():
                        seed_assessments.setdefault(sd, "timeout")
                    else:
                        exc = stask.exception()
                        if exc:
                            seed_assessments.setdefault(sd, "error")
                        else:
                            r = stask.result()
                            seed_assessments.setdefault(sd, r["assessment"])
                            seed_org_names.setdefault(sd, r["org_name"])
                            if r.get("co_hosted_seeds"):
                                seed_cross_verification.setdefault(sd, r["co_hosted_seeds"])

        # Phase 2: Seed-dependent strategies (B + optional second org search)
        dependent_tasks: list[asyncio.Task[list[tuple[str, _DomainAccum]]]] = []

        # Extra org searches from seed-derived org names
        seen_org_searches: set[str] = set()
        for sd, org_name in seed_org_names.items():
            if (
                org_name
                and org_name not in seen_org_searches
                and org_name_similarity(org_name, entity.company_name) < 0.95
            ):
                seen_org_searches.add(org_name)
                dependent_tasks.append(
                    asyncio.create_task(
                        self._strategy_org_search(org_name, errors),
                        name=f"org_search_seed:{sd}",
                    )
                )

        # Strategy B per seed (parallel) — tagged with seed name
        for sd in seeds:
            dependent_tasks.append(
                asyncio.create_task(
                    self._strategy_seed_expansion(sd, entity.company_name, errors),
                    name=f"seed_expansion:{sd}",
                )
            )

        # Gather all strategy results under the remaining time budget (minus 10s reserve)
        all_strategy_tasks = independent_tasks + dependent_tasks
        strategy_budget = _remaining() - 10.0
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*all_strategy_tasks, return_exceptions=True),
                timeout=max(1.0, strategy_budget),
            )
            _collect(results)
        except TimeoutError:
            timed_out = True
            errors.append("Strategy phase timed out")
            # Collect any completed results
            for task in all_strategy_tasks:
                if task.done() and not task.cancelled():
                    exc = task.exception()
                    if exc:
                        errors.append(str(exc))
                    else:
                        _collect([task.result()])
                elif not task.done():
                    task.cancel()

        # Cross-seed detection: if a domain has seed-tagged sources from 2+ seeds
        if len(seeds) > 1:
            self._apply_cross_seed_boost(domain_evidence, seeds)

        # Step 3: DNS resolution for all discovered domains
        all_domains = list(domain_evidence.keys())
        if all_domains and _remaining() > 2.0:
            try:
                resolve_map = await asyncio.wait_for(
                    self._dns.bulk_resolve(all_domains),
                    timeout=_remaining() - 2.0,
                )
                for domain, resolves in resolve_map.items():
                    domain_evidence[domain].resolves = resolves
            except TimeoutError:
                timed_out = True
                errors.append("DNS resolution timed out")

        # Step 3b: GeoDNS rescue for non-resolving domains (deep mode)
        if self.config.deep_mode and _remaining() > 3.0:
            failed_domains = [d for d, acc in domain_evidence.items() if not acc.resolves]
            if failed_domains:
                log.info("scout.geodns_rescue", count=len(failed_domains))
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        geodns_map = await asyncio.wait_for(
                            self._dns.bulk_geodns_resolve(failed_domains, client),
                            timeout=max(1.0, _remaining() - 3.0),
                        )
                    for domain, did_resolve in geodns_map.items():
                        if not did_resolve:
                            continue
                        accum = domain_evidence[domain]
                        accum.resolves = True
                        accum.sources.add("geodns")
                        accum.evidence.append(
                            EvidenceRecord(
                                source_type="geodns",
                                description="Resolved via Shodan GeoDNS (global)",
                            )
                        )
                        log.info("scout.geodns_rescued", domain=domain)
                except TimeoutError:
                    timed_out = True
                    errors.append("GeoDNS resolution timed out")

        # Step 3c: RDAP corroboration on top resolving candidates
        if _remaining() > 5.0:
            try:
                await asyncio.wait_for(
                    self._rdap_corroborate(domain_evidence, entity.company_name),
                    timeout=min(15.0, _remaining() - 3.0),
                )
            except TimeoutError:
                errors.append("RDAP corroboration timed out")

        # Step 4: confidence scoring and infrastructure comparison
        confirmed_domains: list[str] = []
        for domain, accum in domain_evidence.items():
            accum.confidence = self._score_confidence(
                accum,
                entity.company_name,
                seeds,
                domain=domain,
            )
            if accum.confidence >= self.config.seed_confirm_threshold:
                confirmed_domains.append(domain)

        # Infrastructure sharing boost (compare against highest-confidence seed)
        reference: str | None = None
        if seeds:
            # Pick highest-confidence confirmed seed
            best_seed_conf = -1.0
            for sd in seeds:
                sd_base = extract_base_domain(sd)
                if sd_base and sd_base in domain_evidence:
                    conf = domain_evidence[sd_base].confidence
                    if conf > best_seed_conf:
                        best_seed_conf = conf
                        reference = sd_base
            if reference is None:
                reference = extract_base_domain(seeds[0])
        if not reference and confirmed_domains:
            reference = confirmed_domains[0]
        if reference and len(domain_evidence) <= 50 and _remaining() > 1.0:
            await self._infra_boost(reference, domain_evidence)

        # Build output
        domains = self._build_output(domain_evidence, seeds)

        elapsed = time.monotonic() - t0

        # Record metrics
        status = "timeout" if timed_out else ("error" if errors else "ok")
        inc(SCANS_TOTAL, status=status)
        observe(SCAN_DURATION_SECONDS, elapsed)
        observe(DOMAINS_FOUND, float(len(domains)))

        run_meta = RunMetadata(
            tool_version=_pkg_version("domain-scout-ct"),
            timestamp=datetime.now(UTC),
            elapsed_seconds=round(elapsed, 2),
            domains_found=len(domains),
            timed_out=timed_out,
            seed_count=len(seeds),
            errors=errors,
            config=self.config.to_dict(),
        )
        return ScoutResult(
            entity=entity,
            domains=domains,
            seed_domain_assessment=seed_assessments,
            seed_cross_verification=seed_cross_verification,
            run_metadata=run_meta,
        )

    # --- Step 1: Seed validation ---

    async def _validate_seed(
        self,
        seed: str,
        company_name: str,
        all_seeds: list[str],
        errors: list[str],
        seed_to_base: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        """Returns dict with assessment, org_name, and co_hosted_seeds."""
        resolves = await self._dns.resolves(seed)

        rdap_org: str | None = None
        try:
            rdap_org = await self._rdap.get_registrant_org(seed)
        except Exception as exc:
            errors.append(f"RDAP lookup failed for {seed}: {exc}")

        # Also check CT for the org name on certs
        ct_records = await self._ct.search_by_domain(seed)
        cert_orgs: set[str] = set()

        # Build reverse lookup: base domain -> original seed domain (excluding current seed)
        if seed_to_base is None:
            seed_to_base = {s: extract_base_domain(s) for s in all_seeds}

        base_to_seed = {
            base: s for s, base in seed_to_base.items() if s != seed and base is not None
        }

        co_hosted_seeds: list[str] = []

        for rec in ct_records:
            org = rec.get("org_name")
            if (
                isinstance(org, str)
                and org
                # Skip DV certs where O= is just the domain itself
                and not org.lstrip("*.").endswith(("." + seed, seed))
            ):
                cert_orgs.add(org)
            # Check if other seeds share this cert
            sans = _extract_sans(rec)
            san_bases = {b for s in sans if is_valid_domain(s) and (b := extract_base_domain(s))}
            for matched_base in san_bases & base_to_seed.keys():
                other_seed = base_to_seed[matched_base]
                if other_seed not in co_hosted_seeds:
                    co_hosted_seeds.append(other_seed)

        # Pick best org name from any source
        best_org: str | None = None
        best_score = 0.0
        for org in [rdap_org, *cert_orgs]:
            if org:
                score = org_name_similarity(org, company_name)
                if score > best_score:
                    best_score = score
                    best_org = org

        # If the seed domain slug itself closely matches the company name, that's signal too
        seed_slug = extract_base_domain(seed)
        if seed_slug:
            # e.g., "paloaltonetworks" from "paloaltonetworks.com" vs "Palo Alto Networks"
            slug_part = seed_slug.split(".")[0]
            slug_score = org_name_similarity(slug_part, company_name)
            if slug_score > best_score:
                best_score = slug_score

        if best_score >= self.config.seed_confirm_threshold:
            assessment = "confirmed"
        elif resolves:
            assessment = "suspicious"
        else:
            assessment = "invalid"

        return {
            "seed": seed,
            "assessment": assessment,
            "org_name": best_org,
            "co_hosted_seeds": co_hosted_seeds,
        }

    # --- Step 2A: Organization name search ---

    async def _strategy_org_search(
        self,
        org_name: str,
        errors: list[str],
        source_tag: str = "ct_org_match",
    ) -> list[tuple[str, _DomainAccum]]:
        results: list[tuple[str, _DomainAccum]] = []
        try:
            records = await self._ct.search_by_org(org_name)
        except Exception as exc:
            inc(SOURCE_ERRORS_TOTAL, source="ct")
            errors.append(f"CT org search failed: {exc}")
            return results

        for rec in records:
            cert_org = rec.get("org_name")
            if not isinstance(cert_org, str) or not cert_org:
                continue
            # Only keep certs where the org matches our target
            similarity = org_name_similarity(cert_org, org_name)
            if similarity < self.config.org_match_threshold:
                continue

            sans = _extract_sans(rec)
            cn = rec.get("common_name", "")
            all_names = _collect_cert_names(sans, cn)

            for name in all_names:
                if not is_valid_domain(name):
                    continue
                base = extract_base_domain(name)
                if not base:
                    continue
                accum = _DomainAccum()
                accum.sources.add(source_tag)
                desc = f"Cert org '{cert_org}' matches target (score={similarity:.2f})"
                accum.evidence.append(
                    EvidenceRecord(
                        source_type=source_tag,
                        description=desc,
                        cert_id=_int_or_none(rec.get("cert_id")),
                        cert_org=cert_org,
                        similarity_score=round(similarity, 4),
                    )
                )
                accum.cert_org_names.add(cert_org)
                nb = rec.get("not_before")
                na = rec.get("not_after")
                if nb:
                    accum.update_times(nb, na)
                results.append((base, accum))

        return results

    # --- Step 2B: Seed domain expansion ---

    async def _strategy_seed_expansion(
        self, seed_domain: str, company_name: str, errors: list[str]
    ) -> list[tuple[str, _DomainAccum]]:
        results: list[tuple[str, _DomainAccum]] = []
        try:
            records = await self._ct.search_by_domain(seed_domain)
        except Exception as exc:
            inc(SOURCE_ERRORS_TOTAL, source="ct")
            errors.append(f"CT seed expansion failed: {exc}")
            return results

        seed_base = extract_base_domain(seed_domain)

        for rec in records:
            sans = _extract_sans(rec)
            cn = rec.get("common_name", "")
            cert_org = rec.get("org_name")
            all_names = _collect_cert_names(sans, cn)

            # Detect CDN/multi-tenant certs: many unrelated base domains + org mismatch
            unique_bases = {extract_base_domain(s) for s in sans if is_valid_domain(s)} - {None}
            org_sim = (
                org_name_similarity(cert_org, company_name)
                if isinstance(cert_org, str) and cert_org
                else 0.0
            )
            is_cdn_cert = len(unique_bases) > 10 and org_sim < self.config.org_match_threshold

            for name in all_names:
                if not is_valid_domain(name):
                    continue
                base = extract_base_domain(name)
                if not base:
                    continue

                accum = _DomainAccum()

                # Is this a SAN on a cert that also covers the seed domain?
                has_seed_san = any(
                    extract_base_domain(s) == seed_base for s in sans if is_valid_domain(s)
                )

                if base == seed_base:
                    accum.sources.add(f"ct_seed_subdomain:{seed_domain}")
                    accum.evidence.append(
                        EvidenceRecord(
                            source_type="ct_seed_subdomain",
                            description=f"Subdomain of seed domain {seed_domain}",
                            seed_domain=seed_domain,
                        )
                    )
                elif has_seed_san:
                    # Skip non-seed SANs on CDN certs — Strategy A handles org-matched domains
                    if is_cdn_cert:
                        continue
                    accum.sources.add(f"ct_san_expansion:{seed_domain}")
                    accum.evidence.append(
                        EvidenceRecord(
                            source_type="ct_san_expansion",
                            description=f"Found on same cert as seed domain {seed_domain}",
                            seed_domain=seed_domain,
                        )
                    )
                else:
                    accum.sources.add(f"ct_seed_related:{seed_domain}")
                    accum.evidence.append(
                        EvidenceRecord(
                            source_type="ct_seed_related",
                            description=f"Found in CT search for {seed_domain}",
                            seed_domain=seed_domain,
                        )
                    )

                if isinstance(cert_org, str) and cert_org:
                    accum.cert_org_names.add(cert_org)
                    sim = org_name_similarity(cert_org, company_name)
                    if sim >= self.config.org_match_threshold:
                        accum.sources.add("ct_org_match")
                        desc = f"Cert org '{cert_org}' matches target (score={sim:.2f})"
                        accum.evidence.append(
                            EvidenceRecord(
                                source_type="ct_org_match",
                                description=desc,
                                cert_id=_int_or_none(rec.get("cert_id")),
                                cert_org=cert_org,
                                similarity_score=round(sim, 4),
                            )
                        )

                nb = rec.get("not_before")
                na = rec.get("not_after")
                if nb:
                    accum.update_times(nb, na)
                results.append((base, accum))

        return results

    # --- Step 2C: Domain guessing ---

    async def _strategy_domain_guess(
        self, company_name: str, location: str | None, errors: list[str]
    ) -> list[tuple[str, _DomainAccum]]:
        slugs = domain_from_company_name(company_name)
        # Also try with location keywords
        if location:
            loc_words = [w.lower().strip(",. ") for w in location.split() if len(w) > 2]
            for slug in list(slugs):
                for lw in loc_words[:2]:
                    slugs.append(slug + lw)

        candidates: list[str] = []
        for slug in slugs:
            for tld in self.config.guess_tlds:
                candidates.append(slug + tld)

        # DNS resolve all candidates
        resolve_map = await self._dns.bulk_resolve(candidates)

        # Return resolving candidates at low confidence (Strategy A handles CT matching)
        results: list[tuple[str, _DomainAccum]] = []
        for domain, does_resolve in resolve_map.items():
            if not does_resolve:
                continue
            base = extract_base_domain(domain)
            if not base:
                continue
            accum = _DomainAccum()
            accum.sources.add("dns_guess")
            accum.evidence.append(
                EvidenceRecord(
                    source_type="dns_guess",
                    description="Guessed from company name, resolves in DNS",
                )
            )
            accum.resolves = True
            results.append((base, accum))

        return results

    # --- Cross-seed detection ---

    @staticmethod
    def _apply_cross_seed_boost(domain_evidence: dict[str, _DomainAccum], seeds: list[str]) -> None:
        """Add cross_seed_verified source to domains found from 2+ independent seeds.

        Requires at least one strong source (ct_san_expansion or ct_seed_subdomain).
        Two ct_seed_related from different seeds is too weak to cross-verify.
        """
        strong_prefixes = ("ct_san_expansion:", "ct_seed_subdomain:")
        for accum in domain_evidence.values():
            contributing_seeds = _extract_contributing_seeds(accum.sources)
            if len(contributing_seeds) >= 2:
                has_strong = any(s.startswith(strong_prefixes) for s in accum.sources)
                if not has_strong:
                    continue
                seeds_str = ", ".join(sorted(contributing_seeds))
                accum.sources.add("cross_seed_verified")
                accum.evidence.append(
                    EvidenceRecord(
                        source_type="cross_seed_verified",
                        description=f"Cross-verified: found from seeds {seeds_str}",
                    )
                )

    # --- Step 3: Confidence scoring ---

    def _score_confidence(
        self,
        accum: _DomainAccum,
        company_name: str,
        seed_domains: list[str],
        domain: str = "",
    ) -> float:
        # Learned scorer path (opt-in via config)
        if self.config.use_learned_scorer and domain and accum.cert_org_names:
            from domain_scout.scorer import score_confidence as _learned_score

            best_sim = max(
                (org_name_similarity(cert_org, company_name) for cert_org in accum.cert_org_names),
                default=0.0,
            )
            # Count unique cert IDs for evidence_density
            cert_ids: set[int] = set()
            for ev in accum.evidence:
                if ev.cert_id is not None:
                    cert_ids.add(ev.cert_id)

            # Extract max RDAP registrant similarity from evidence
            rdap_sim = max(
                (
                    ev.similarity_score
                    for ev in accum.evidence
                    if ev.source_type == "rdap_registrant_match" and ev.similarity_score is not None
                ),
                default=0.0,
            )

            return _learned_score(
                domain=domain,
                company_name=company_name,
                best_similarity=best_sim,
                sources=accum.sources,
                cert_org_names=accum.cert_org_names,
                resolves=accum.resolves,
                evidence_count=len(accum.evidence),
                unique_cert_count=len(cert_ids),
                rdap_similarity=rdap_sim,
            )

        # Heuristic scorer (default)
        # Phase 1: base score from source type
        score = 0.0

        if "cross_seed_verified" in accum.sources:
            score = max(score, 0.90)
        if "ct_org_match" in accum.sources:
            score = max(score, 0.85)
        if "ct_subsidiary_match" in accum.sources:
            score = max(score, 0.80)
        if any(s.startswith("ct_san_expansion:") for s in accum.sources):
            score = max(score, 0.80)
        if any(s.startswith("ct_seed_subdomain:") for s in accum.sources):
            score = max(score, 0.75)
        if any(s.startswith("ct_seed_related:") for s in accum.sources):
            score = max(score, 0.40)
        if "dns_guess" in accum.sources and "ct_org_match" not in accum.sources:
            score = max(score, 0.30)

        # Phase 2: corroboration level adjustment
        # dns_guess bypasses corroboration — it already implies resolution
        if score <= 0.30:
            return round(score, 2)

        has_resolves = accum.resolves
        has_rdap = "rdap_registrant_match" in accum.sources

        best_sim = max(
            (org_name_similarity(cert_org, company_name) for cert_org in accum.cert_org_names),
            default=0.0,
        )
        has_high_sim = best_sim > 0.9

        has_multi_source = len(accum.sources) >= 3

        if has_resolves and (has_rdap or has_high_sim) and has_multi_source:
            adjustment = 0.10  # Level 3: strong corroboration
        elif has_resolves and (has_rdap or has_high_sim or has_multi_source):
            adjustment = 0.05  # Level 2: moderate corroboration
        elif has_resolves:
            adjustment = 0.00  # Level 1: resolves only
        else:
            adjustment = -0.05  # Level 0: no resolution

        score = min(1.0, max(0.0, score + adjustment))

        return round(score, 2)

    async def _infra_boost(self, reference: str, evidence: dict[str, _DomainAccum]) -> None:
        """Small confidence boost for domains sharing infra with a reference domain."""
        # Select top candidates by confidence, capped
        candidates = [
            (domain, accum)
            for domain, accum in evidence.items()
            if domain != reference and accum.confidence >= 0.3
        ]
        candidates.sort(key=lambda x: x[1].confidence, reverse=True)
        candidates = candidates[: self.config.infra_check_max]

        async def _check(domain: str, accum: _DomainAccum) -> None:
            try:
                shared = await self._dns.shares_infrastructure(reference, domain)
                if shared:
                    accum.sources.add("shared_infra")
                    accum.evidence.append(
                        EvidenceRecord(
                            source_type="shared_infra",
                            description=f"Shares infrastructure with {reference}",
                        )
                    )
                    # Cap so infra boost can't exceed the +0.10 total boost limit.
                    # Only cross_seed_verified (0.90 base) should reach 1.00.
                    max_conf = 1.0 if "cross_seed_verified" in accum.sources else 0.95
                    accum.confidence = round(min(max_conf, accum.confidence + 0.05), 2)
            except Exception:
                pass

        try:
            await asyncio.wait_for(
                asyncio.gather(*[_check(d, a) for d, a in candidates]),
                timeout=10.0,
            )
        except TimeoutError:
            log.warning("scout.infra_boost_timeout", checked=len(candidates))

    # --- RDAP corroboration ---

    async def _rdap_corroborate(
        self, domain_evidence: dict[str, _DomainAccum], company_name: str
    ) -> None:
        """Query RDAP on top resolving candidates and add corroborating evidence."""
        # Select resolving candidates without existing rdap_registrant_match
        candidates = [
            (domain, accum)
            for domain, accum in domain_evidence.items()
            if accum.resolves and "rdap_registrant_match" not in accum.sources
        ]
        # Sort by source count (descending) as a proxy for importance
        candidates.sort(key=lambda x: len(x[1].sources), reverse=True)
        candidates = candidates[: self.config.rdap_corroborate_max]

        if not candidates:
            return

        async def _check(domain: str, accum: _DomainAccum) -> None:
            try:
                rdap_org = await self._rdap.get_registrant_org(domain)
                if not rdap_org:
                    return
                sim = org_name_similarity(rdap_org, company_name)
                if sim >= self.config.org_match_threshold:
                    accum.sources.add("rdap_registrant_match")
                    accum.rdap_org = rdap_org
                    accum.evidence.append(
                        EvidenceRecord(
                            source_type="rdap_registrant_match",
                            description=(
                                f"RDAP registrant '{rdap_org}' matches target (score={sim:.2f})"
                            ),
                            rdap_org=rdap_org,
                            similarity_score=round(sim, 4),
                        )
                    )
            except Exception as exc:
                log.debug("scout.rdap_corroborate_error", domain=domain, error=str(exc))

        try:
            await asyncio.wait_for(
                asyncio.gather(*[_check(d, a) for d, a in candidates]),
                timeout=15.0,
            )
        except TimeoutError:
            log.warning("scout.rdap_corroborate_timeout", checked=len(candidates))

    # --- Step 4: Build output ---

    def _build_output(
        self, evidence: dict[str, _DomainAccum], seed_domains: list[str]
    ) -> list[DiscoveredDomain]:
        domains: list[DiscoveredDomain] = []
        seed_bases = {extract_base_domain(sd) for sd in seed_domains} - {None}

        for domain, accum in evidence.items():
            if accum.confidence < self.config.inclusion_threshold:
                continue
            if (
                not accum.resolves
                and domain not in seed_bases
                and not self.config.include_non_resolving
            ):
                continue

            contributing_seeds = sorted(_extract_contributing_seeds(accum.sources))

            # Deduplicate evidence records
            seen: set[tuple[str, str | None, int | None]] = set()
            deduped: list[EvidenceRecord] = []
            for ev in accum.evidence:
                key = (ev.source_type, ev.seed_domain, ev.cert_id)
                if key not in seen:
                    seen.add(key)
                    deduped.append(ev)

            domains.append(
                DiscoveredDomain(
                    domain=domain,
                    confidence=accum.confidence,
                    sources=sorted(accum.sources),
                    evidence=deduped,
                    cert_org_names=sorted(accum.cert_org_names),
                    first_seen=_parse_time(accum.first_seen),
                    last_seen=_parse_time(accum.last_seen),
                    resolves=accum.resolves,
                    rdap_org=accum.rdap_org,
                    is_seed=(domain in seed_bases),
                    seed_sources=contributing_seeds,
                )
            )

        domains.sort(key=lambda d: d.confidence, reverse=True)
        return domains


_SEED_SOURCE_PREFIXES = (
    "ct_san_expansion:",
    "ct_seed_subdomain:",
    "ct_seed_related:",
)


def _extract_contributing_seeds(sources: set[str]) -> set[str]:
    """Extract the set of seed domains that contributed to a source set."""
    seeds: set[str] = set()
    for src in sources:
        for prefix in _SEED_SOURCE_PREFIXES:
            if src.startswith(prefix):
                seeds.add(src[len(prefix) :])
    return seeds


def _int_or_none(val: object) -> int | None:
    """Safely extract an int from a dict value, or None."""
    return val if isinstance(val, int) else None


def _collect_cert_names(sans: list[str], cn: Any) -> list[str]:
    """Deduplicate SAN list with common name into a single name list."""
    names = set(sans)
    if isinstance(cn, str) and cn:
        names.add(cn)
    return list(names)


def _extract_sans(rec: dict[str, object]) -> list[str]:
    """Extract SAN DNS names from a cert record."""
    raw = rec.get("san_dns_names")
    sans: list[str] = raw if isinstance(raw, list) else []
    return sans


def _normalize_time(val: object) -> str | None:
    """Normalize a datetime or string to ISO string for consistent comparison.

    CT Postgres returns datetime objects, JSON API and cache return strings.
    Normalizing to ISO strings prevents TypeError on mixed-type comparison.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, str):
        if not val:
            return None
        try:
            return datetime.fromisoformat(val).isoformat()
        except ValueError:
            return val
    return str(val)


def _parse_time(val: str | None) -> datetime | None:
    """Parse an ISO 8601 string back to datetime for Pydantic output."""
    if val is None:
        return None
    return datetime.fromisoformat(val)


class _DomainAccum:
    """Mutable accumulator for evidence about a single domain."""

    __slots__ = (
        "sources",
        "evidence",
        "cert_org_names",
        "first_seen",
        "last_seen",
        "resolves",
        "rdap_org",
        "confidence",
    )

    def __init__(self) -> None:
        self.sources: set[str] = set()
        self.evidence: list[EvidenceRecord] = []
        self.cert_org_names: set[str] = set()
        self.first_seen: str | None = None
        self.last_seen: str | None = None
        self.resolves: bool = False
        self.rdap_org: str | None = None
        self.confidence: float = 0.0

    def merge(self, other: _DomainAccum) -> None:
        self.sources |= other.sources
        self.evidence.extend(other.evidence)
        self.cert_org_names |= other.cert_org_names
        o_first = _normalize_time(other.first_seen)
        if o_first and (self.first_seen is None or o_first < self.first_seen):
            self.first_seen = o_first
        o_last = _normalize_time(other.last_seen)
        if o_last and (self.last_seen is None or o_last > self.last_seen):
            self.last_seen = o_last
        self.resolves = self.resolves or other.resolves
        if self.rdap_org is None and other.rdap_org is not None:
            self.rdap_org = other.rdap_org

    def update_times(self, not_before: object, not_after: object) -> None:
        nb = _normalize_time(not_before)
        na = _normalize_time(not_after)
        if nb and (self.first_seen is None or nb < self.first_seen):
            self.first_seen = nb
        if na and (self.last_seen is None or na > self.last_seen):
            self.last_seen = na
