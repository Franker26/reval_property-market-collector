from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class Location(BaseModel):
    country: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    neighborhood: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


class PropertyListing(BaseModel):
    # ── Identidad ─────────────────────────────────────────────────────────────
    url: str
    portal: str
    external_id: Optional[str] = None
    captured_at: Optional[datetime] = None

    # ── Transacción ───────────────────────────────────────────────────────────
    operation_type: Optional[str] = None    # "venta" | "alquiler"
    currency: Optional[str] = None          # "USD" | "ARS"
    precio: Optional[int] = None
    expenses: Optional[int] = None

    # ── Propiedad ─────────────────────────────────────────────────────────────
    title: Optional[str] = None
    description: Optional[str] = None
    tipo: Optional[str] = None
    ambientes: Optional[int] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None

    # ── Superficies ───────────────────────────────────────────────────────────
    superficie_total: Optional[float] = None
    superficie_cubierta: Optional[float] = None
    superficie_semicubierta: Optional[float] = None
    superficie_descubierta: Optional[float] = None

    # ── Atributos físicos ─────────────────────────────────────────────────────
    antiguedad: Optional[int] = None
    orientacion: Optional[str] = None
    piso: Optional[int] = None
    cochera: Optional[bool] = None
    pileta: Optional[bool] = None

    # ── Ubicación ─────────────────────────────────────────────────────────────
    direccion: Optional[str] = None
    location: Optional[Location] = None

    # ── Media ─────────────────────────────────────────────────────────────────
    imagen_url: Optional[str] = None

    # ── Mercado ───────────────────────────────────────────────────────────────
    dias_mercado: Optional[int] = None

    # ── Vendedor ──────────────────────────────────────────────────────────────
    seller_name: Optional[str] = None
    seller_type: Optional[str] = None
