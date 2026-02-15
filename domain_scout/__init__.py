"""domain-scout: Discover internet domains associated with a business entity."""

from domain_scout.models import DiscoveredDomain, EntityInput, ScoutResult
from domain_scout.scout import Scout

__all__ = ["Scout", "EntityInput", "DiscoveredDomain", "ScoutResult"]
