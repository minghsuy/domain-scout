import re

with open("domain_scout/scout.py", "r") as f:
    text = f.read()

text = text.replace("from typing import TYPE_CHECKING, Any\n", "from dataclasses import dataclass, field\nfrom typing import TYPE_CHECKING, Any\n")

dataclass_code = """
@dataclass
class _DiscoveryContext:
    entity: EntityInput
    start_time: float
    total_budget: float
    seeds: list[str]
    errors: list[str] = field(default_factory=list)
    timed_out: bool = False
    domain_evidence: dict[str, "_DomainAccum"] = field(default_factory=dict)
    seed_assessments: dict[str, str] = field(default_factory=dict)
    seed_org_names: dict[str, str | None] = field(default_factory=dict)
    seed_cross_verification: dict[str, list[str]] = field(default_factory=dict)
    seed_ct_records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def remaining_time(self) -> float:
        import time
        return max(0.0, self.total_budget - (time.monotonic() - self.start_time))


class Scout:
"""

text = text.replace("class Scout:\n", dataclass_code)

with open("domain_scout/scout.py", "w") as f:
    f.write(text)
