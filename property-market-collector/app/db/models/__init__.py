"""
Modelos SQLAlchemy para Reval Market Intelligence.

Estructura:
  portals.py               — MarketSource (registro de portales, genérico)
  listings.py              — ListingEntity, ListingSnapshot (genérico)
  runs.py                  — CollectionRun, CollectionError (genérico)
  location_normalization.py — ListingLocationNormalization (capa geográfica)
  market_facts.py          — ListingMarketFacts (capa analítica derivada)
  zonaprop/
    segments.py            — ZonapropSegment, ZonapropSegmentSnapshot
    scan_queue.py          — ZonapropSegmentScanQueue
"""
from .base import Base
from .portals import MarketSource
from .listings import ListingEntity, ListingSnapshot
from .runs import CollectionRun, CollectionError
from .location_normalization import ListingLocationNormalization
from .market_facts import ListingMarketFacts
from .zonaprop.segments import ZonapropSegment, ZonapropSegmentSnapshot, ZonapropSegmentScanHistory
from .zonaprop.scan_queue import ZonapropSegmentScanQueue

__all__ = [
    "Base",
    "MarketSource",
    "ListingEntity",
    "ListingSnapshot",
    "CollectionRun",
    "CollectionError",
    "ListingLocationNormalization",
    "ListingMarketFacts",
    "ZonapropSegment",
    "ZonapropSegmentSnapshot",
    "ZonapropSegmentScanHistory",
    "ZonapropSegmentScanQueue",
]
