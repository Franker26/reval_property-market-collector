"""Endpoints para listings y snapshots."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.repositories import listings as listings_repo
from app.repositories import snapshots as snapshots_repo

router = APIRouter(prefix="/listings", tags=["listings"])


@router.get("")
async def list_listings(
    source_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    items = await listings_repo.list_all(db, source_id=source_id, status=status, limit=limit, offset=offset)
    return [_listing_dict(l) for l in items]


@router.get("/{listing_id}")
async def get_listing(listing_id: int, db: AsyncSession = Depends(get_db)):
    listing = await listings_repo.get_by_id(db, listing_id)
    if not listing:
        raise HTTPException(404, "Listing no encontrado")
    return _listing_dict(listing)


@router.get("/{listing_id}/snapshots")
async def get_snapshots(
    listing_id: int,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    listing = await listings_repo.get_by_id(db, listing_id)
    if not listing:
        raise HTTPException(404, "Listing no encontrado")
    snaps = await snapshots_repo.list_for_listing(db, listing_id, limit=limit, offset=offset)
    return [_snapshot_dict(s) for s in snaps]


def _listing_dict(l) -> dict:
    return {
        "id": l.id,
        "source_id": l.source_id,
        "external_id": l.external_id,
        "canonical_url": l.canonical_url,
        "operation_type": l.operation_type,
        "property_type": l.property_type,
        "status": l.status,
        "price_amount": float(l.price_amount) if l.price_amount is not None else None,
        "price_currency": l.price_currency,
        "surface_total": float(l.surface_total) if l.surface_total is not None else None,
        "rooms": l.rooms,
        "bedrooms": l.bedrooms,
        "bathrooms": l.bathrooms,
        "garages": l.garages,
        "address": l.address,
        "neighborhood": l.neighborhood,
        "city": l.city,
        "province_name": l.province_name,
        "seller_name": l.seller_name,
        "seller_type": l.seller_type,
        "content_hash": l.content_hash,
        "first_seen_at": l.first_seen_at.isoformat() if l.first_seen_at else None,
        "last_seen_at": l.last_seen_at.isoformat() if l.last_seen_at else None,
        "last_changed_at": l.last_changed_at.isoformat() if l.last_changed_at else None,
    }


def _snapshot_dict(s) -> dict:
    return {
        "id": s.id,
        "listing_id": s.listing_id,
        "captured_at": s.captured_at.isoformat() if s.captured_at else None,
        "content_hash": s.content_hash,
        "status": s.status,
        "price_amount": float(s.price_amount) if s.price_amount is not None else None,
        "price_currency": s.price_currency,
        "expenses_amount": float(s.expenses_amount) if s.expenses_amount is not None else None,
        "surface_total": float(s.surface_total) if s.surface_total is not None else None,
        "rooms": s.rooms,
        "bedrooms": s.bedrooms,
        "bathrooms": s.bathrooms,
        "garages": s.garages,
        "address": s.address,
        "neighborhood": s.neighborhood,
        "city": s.city,
        "seller_name": s.seller_name,
        "seller_type": s.seller_type,
    }
