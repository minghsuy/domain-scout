#!/usr/bin/env python3
"""Auto-label high-confidence scout results as eval ground truth.

Reads scout_results/*.json from ct-entity-resolution benchmark,
filters to high-confidence entities, and appends to eval_ground_truth.yaml.

Usage:
    python scripts/auto_label.py --scout-results ~/ct-entity-resolution/benchmark/scout_results \
        --output domain_scout/eval_ground_truth.yaml \
        --min-confidence 0.85 --min-domains 2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


# Domains appearing in 3+ entities — likely shared/CDN, not company-owned
SHARED_DOMAIN_BLOCKLIST = frozenset({
    "cert-manager.com",
    "key4.ch",
    "ubs.com",
    "ubssecurities.com",
    "from.ubs",
})


def load_existing_companies(gt_path: Path) -> set[str]:
    """Return set of company_name values already in ground truth."""
    if not gt_path.exists():
        return set()
    with gt_path.open() as f:
        entries = yaml.safe_load(f) or []
    return {e["company_name"] for e in entries}


def load_scout_result(path: Path) -> dict | None:
    """Load a scout result JSON, return None if unusable."""
    with path.open() as f:
        data = json.load(f)
    if data.get("timed_out") or data.get("error"):
        return None
    sr = data.get("scout_result")
    if not sr or not sr.get("domains"):
        return None
    return data


def extract_label(
    data: dict,
    *,
    min_confidence: float,
    min_domains: int,
) -> dict | None:
    """Extract a ground truth entry from a scout result.

    Returns a dict matching GroundTruthEntry schema, or None if
    the entity doesn't meet quality thresholds.
    """
    sr = data["scout_result"]
    domains = sr["domains"]

    owned = []
    not_owned = []

    for d in domains:
        dom = d["domain"]
        if dom in SHARED_DOMAIN_BLOCKLIST:
            not_owned.append(dom)
            continue
        if d["confidence"] >= min_confidence and d.get("resolves"):
            owned.append(dom)

    if len(owned) < min_domains:
        return None

    ticker = data.get("ticker", Path(data.get("_filename", "unknown")).stem)
    entry = {
        "label_id": f"{ticker.lower()}-auto-20260224",
        "company_name": data["canonical_name"],
        "seeds": [],  # no seeds used in batch runs
        "owned_domains": owned,
    }
    if not_owned:
        entry["not_owned"] = not_owned
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-label scout results as ground truth")
    parser.add_argument(
        "--scout-results",
        type=Path,
        default=Path.home() / "ct-entity-resolution" / "benchmark" / "scout_results",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("domain_scout/eval_ground_truth.yaml"),
    )
    parser.add_argument("--min-confidence", type=float, default=0.85)
    parser.add_argument("--min-domains", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true", help="Print stats without writing")
    args = parser.parse_args()

    existing = load_existing_companies(args.output)
    print(f"Existing ground truth: {len(existing)} companies")

    # Load existing entries to preserve them
    if args.output.exists():
        with args.output.open() as f:
            existing_entries = yaml.safe_load(f) or []
    else:
        existing_entries = []

    new_entries = []
    skipped_existing = 0
    skipped_quality = 0
    skipped_unusable = 0

    result_files = sorted(args.scout_results.glob("*.json"))
    print(f"Scanning {len(result_files)} result files...")

    for path in result_files:
        data = load_scout_result(path)
        if data is None:
            skipped_unusable += 1
            continue

        if data["canonical_name"] in existing:
            skipped_existing += 1
            continue

        data["_filename"] = path.name
        entry = extract_label(
            data,
            min_confidence=args.min_confidence,
            min_domains=args.min_domains,
        )
        if entry is None:
            skipped_quality += 1
            continue

        new_entries.append(entry)

    print(f"\nResults:")
    print(f"  New auto-labels: {len(new_entries)}")
    print(f"  Skipped (already in GT): {skipped_existing}")
    print(f"  Skipped (below quality): {skipped_quality}")
    print(f"  Skipped (unusable/empty): {skipped_unusable}")

    total_owned = sum(len(e["owned_domains"]) for e in new_entries)
    total_not_owned = sum(len(e.get("not_owned", [])) for e in new_entries)
    print(f"  Total owned domains: {total_owned}")
    print(f"  Total not_owned domains: {total_not_owned}")

    if new_entries:
        # Domain count distribution
        counts = sorted(len(e["owned_domains"]) for e in new_entries)
        print(f"  Domains per entity: min={counts[0]}, median={counts[len(counts)//2]}, max={counts[-1]}")

    if args.dry_run:
        print("\n[DRY RUN] No files written.")
        # Show a few examples
        for e in new_entries[:3]:
            print(f"\n  {e['label_id']}: {e['company_name']}")
            print(f"    owned ({len(e['owned_domains'])}): {e['owned_domains'][:5]}...")
        return

    # Write combined output
    all_entries = existing_entries + new_entries
    with args.output.open("w") as f:
        f.write(
            "# Ground truth labels for domain-scout evaluation harness.\n"
            '# Each entry maps a (company, seeds) pair to known-owned and known-not-owned domains.\n'
            '# "owned" means the domain is legitimately operated by / affiliated with the company.\n'
            '# "not_owned" means the domain appeared in results but is a false positive '
            "(CDN, shared cert, etc.).\n"
            '# Domains not in either list are treated as "unknown" — conservative precision '
            "counts them against.\n"
            "#\n"
            "# Sources: CT log cert_events data (ct-entity-resolution, 2026-02-19/20, ~1.7M rows),\n"
            "# cross-referenced with public corporate subsidiary records and WHOIS/RDAP.\n"
            "#\n"
            f"# Auto-labeled entries added 2026-02-24 (min_confidence={args.min_confidence}, "
            f"min_domains={args.min_domains})\n\n"
        )
        yaml.dump(all_entries, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(f"\nWrote {len(all_entries)} total entries to {args.output}")


if __name__ == "__main__":
    main()
