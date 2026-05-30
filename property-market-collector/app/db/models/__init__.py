"""
Modelos SQLAlchemy para Reval Market Intelligence.

Estructura:
  portals.py        — MarketSource (registro de portales, genérico)
  listings.py       — ListingEntity, ListingSnapshot (genérico)
  runs.py           — CollectionRun, CollectionError (genérico)
  events.py         — DiscoveryEvent (genérico)
  zonaprop/
    segments.py     — ZonapropSegment, ZonapropSegmentSnapshot
    scan_queue.py   — ZonapropSegmentScanQueue
"""
from .base import Base
from .portals import MarketSource
from .listings import ListingEntity, ListingSnapshot
from .runs import CollectionRun, CollectionError
from .events import DiscoveryEvent
from .zonaprop.segments import ZonapropSegment, ZonapropSegmentSnapshot
from .zonaprop.scan_queue import ZonapropSegmentScanQueue

__all__ = [
    "Base",
    "MarketSource",
    "ListingEntity",
    "ListingSnapshot",
    "CollectionRun",
    "CollectionError",
    "DiscoveryEvent",
    "ZonapropSegment",
    "ZonapropSegmentSnapshot",
    "ZonapropSegmentScanQueue",
]
