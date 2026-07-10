"""Delta reporting: compute differences between two ScoutResult runs."""

from __future__ import annotations

from domain_scout.models import (
    ChangedDomain,
    DeltaReport,
    DeltaSummary,
    DeltaWarning,
    DiscoveredDomain,
    DomainChange,
    ScoutResult,
)

_CONFIDENCE_EPSILON = 0.02


def _scorer_identity(d: DiscoveredDomain) -> tuple[str, str]:
    return (d.scorer_id, d.scorer_version)


def compute_delta(baseline: ScoutResult, current: ScoutResult) -> DeltaReport:
    """Compare two scan results and produce a delta report."""
    warnings = _check_warnings(baseline, current)

    baseline_map = {d.domain: d for d in baseline.domains}
    current_map = {d.domain: d for d in current.domains}

    added_keys = sorted(current_map.keys() - baseline_map.keys())
    removed_keys = sorted(baseline_map.keys() - current_map.keys())
    common_keys = sorted(baseline_map.keys() & current_map.keys())

    added = [current_map[k] for k in added_keys]
    removed = [baseline_map[k] for k in removed_keys]

    scorer_transitions: dict[tuple[tuple[str, str], tuple[str, str]], int] = {}
    changed: list[ChangedDomain] = []
    for key in common_keys:
        old_identity = _scorer_identity(baseline_map[key])
        new_identity = _scorer_identity(current_map[key])
        if old_identity != new_identity:
            pair = (old_identity, new_identity)
            scorer_transitions[pair] = scorer_transitions.get(pair, 0) + 1
        changes = _diff_domain(baseline_map[key], current_map[key])
        if changes:
            changed.append(
                ChangedDomain(
                    domain=key,
                    changes=changes,
                    baseline_confidence=baseline_map[key].confidence,
                    current_confidence=current_map[key].confidence,
                )
            )

    if scorer_transitions:
        total = sum(scorer_transitions.values())
        detail = ", ".join(
            f"{'/'.join(old_id)} -> {'/'.join(new_id)} ({count})"
            for (old_id, new_id), count in sorted(scorer_transitions.items())
        )
        warnings.append(
            DeltaWarning(
                code="scorer_changed",
                message=(
                    f"Scorer identity differs on {total} of {len(common_keys)} common "
                    f"domains ({detail}); confidence deltas on those domains are "
                    f"suppressed as incomparable"
                ),
            )
        )

    summary = DeltaSummary(
        added=len(added),
        removed=len(removed),
        changed=len(changed),
        unchanged=len(common_keys) - len(changed),
        baseline_total=len(baseline.domains),
        current_total=len(current.domains),
    )

    return DeltaReport(
        added=added,
        removed=removed,
        changed=changed,
        summary=summary,
        warnings=warnings,
        baseline_metadata=baseline.run_metadata,
        current_metadata=current.run_metadata,
    )


def _diff_domain(old: DiscoveredDomain, new: DiscoveredDomain) -> list[DomainChange]:
    """Return field-level changes between two versions of the same domain.

    Confidence is only compared when both values come from the same scorer
    identity — a heuristic ladder score and a learned calibrated probability
    (or two differently-versioned scorers) are incomparable, and diffing them
    would report a scorer switch as hundreds of real-world changes (#184).
    compute_delta emits a run-level "scorer_changed" warning instead.
    """
    changes: list[DomainChange] = []

    if (
        _scorer_identity(old) == _scorer_identity(new)
        and abs(old.confidence - new.confidence) >= _CONFIDENCE_EPSILON
    ):
        changes.append(DomainChange(field="confidence", old=old.confidence, new=new.confidence))

    if old.resolves != new.resolves:
        changes.append(DomainChange(field="resolves", old=old.resolves, new=new.resolves))

    if sorted(old.sources) != sorted(new.sources):
        changes.append(
            DomainChange(field="sources", old=sorted(old.sources), new=sorted(new.sources))
        )

    if old.rdap_org != new.rdap_org:
        changes.append(DomainChange(field="rdap_org", old=old.rdap_org, new=new.rdap_org))

    return changes


def _check_warnings(baseline: ScoutResult, current: ScoutResult) -> list[DeltaWarning]:
    """Generate warnings when scan context differs between runs."""
    warnings: list[DeltaWarning] = []

    if baseline.entity.company_name != current.entity.company_name:
        warnings.append(
            DeltaWarning(
                code="company_name_changed",
                message=(
                    f"Company name differs: "
                    f"'{baseline.entity.company_name}' vs '{current.entity.company_name}'"
                ),
            )
        )

    if sorted(baseline.entity.seed_domain) != sorted(current.entity.seed_domain):
        warnings.append(
            DeltaWarning(
                code="seeds_changed",
                message=(
                    f"Seed domains differ: "
                    f"{sorted(baseline.entity.seed_domain)} vs "
                    f"{sorted(current.entity.seed_domain)}"
                ),
            )
        )

    b_cfg = baseline.run_metadata.config
    c_cfg = current.run_metadata.config
    diff_keys = sorted(k for k in b_cfg.keys() | c_cfg.keys() if b_cfg.get(k) != c_cfg.get(k))
    if diff_keys:
        warnings.append(
            DeltaWarning(
                code="config_changed",
                message=f"Config keys differ: {', '.join(diff_keys)}",
            )
        )

    if baseline.run_metadata.timed_out:
        warnings.append(
            DeltaWarning(
                code="baseline_timed_out",
                message="Baseline scan timed out — results may be incomplete",
            )
        )

    if current.run_metadata.timed_out:
        warnings.append(
            DeltaWarning(
                code="current_timed_out",
                message="Current scan timed out — results may be incomplete",
            )
        )

    if baseline.run_metadata.schema_version != current.run_metadata.schema_version:
        warnings.append(
            DeltaWarning(
                code="schema_version_mismatch",
                message=(
                    f"Schema version differs: "
                    f"'{baseline.run_metadata.schema_version}' vs "
                    f"'{current.run_metadata.schema_version}'"
                ),
            )
        )

    return warnings
