"""
Web Search Filters
==================

Result filtering strategies.
"""

from .blacklist import BlacklistFilter
from .privacy import PrivacyFilter
from .deduplication import DeduplicationFilter
from .relevance import RelevanceFilter
from .source_diversity import SourceDiversityFilter
from .freshness import FreshnessBoostFilter

__all__ = [
    "BlacklistFilter",
    "PrivacyFilter",
    "DeduplicationFilter",
    "RelevanceFilter",
    "SourceDiversityFilter",
    "FreshnessBoostFilter",
]
