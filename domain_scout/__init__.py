"""domain-scout: Discover internet domains associated with a business entity."""

from importlib.metadata import version as _pkg_version

from domain_scout._logging import configure_logging
from domain_scout.models import (
    DiscoveredDomain,
    EntityInput,
    EvidenceRecord,
    RunMetadata,
    ScoutResult,
)
from domain_scout.scout import Scout

configure_logging()

__version__ = _pkg_version("domain-scout")

__all__ = [
    "Scout",
    "EntityInput",
    "DiscoveredDomain",
    "EvidenceRecord",
    "RunMetadata",
    "ScoutResult",
    "__version__",
    "configure_logging",
]
