from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ── Sub-models ─────────────────────────────────────────────────────────────────

class ListingInfo(BaseModel):
    """Datos de la publicación en el portal."""
    title: Optional[str] = None
    description: Optional[str] = None
    operation_type: Optional[str] = None    # "venta" | "alquiler"
    dias_mercado: Optional[int] = None
    posting_type: Optional[str] = None      # PROPERTY | UNIT | DEVELOPMENT
    posting_status: Optional[str] = None    # ONLINE | OFFLINE | PAUSED | RESERVED | UNKNOWN
    publication_tier: Optional[str] = None  # free | simple | destacado | superdestacado | exclusive


class PriceInfo(BaseModel):
    """Precio y moneda de la operación."""
    currency: Optional[str] = None          # "USD" | "ARS"
    precio: Optional[int] = None
    expenses: Optional[int] = None


class PropertyInfo(BaseModel):
    """Características físicas del inmueble."""
    tipo: Optional[str] = None
    ambientes: Optional[int] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    superficie_total: Optional[float] = None
    superficie_cubierta: Optional[float] = None
    superficie_semicubierta: Optional[float] = None
    superficie_descubierta: Optional[float] = None
    antiguedad: Optional[int] = None
    orientacion: Optional[str] = None
    piso: Optional[int] = None
    cochera: Optional[bool] = None
    pileta: Optional[bool] = None
    features: dict = Field(default_factory=dict)


class LocationInfo(BaseModel):
    """Ubicación geográfica y visibilidad de dirección."""
    direccion: Optional[str] = None
    country: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    neighborhood: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    address_visibility: Optional[str] = None  # EXACT | APPROXIMATE | HIDDEN | UNKNOWN


class MediaInfo(BaseModel):
    """Material visual de la publicación."""
    imagen_url: Optional[str] = None
    pictures_count: Optional[int] = None    # calculado desde CDN, no viene de la API
    has_video: Optional[bool] = None
    has_tour_360: Optional[bool] = None


class SellerInfo(BaseModel):
    """Datos del anunciante."""
    name: Optional[str] = None
    type: Optional[str] = None              # "inmobiliaria" | "corredor" | "desarrollador" | "particular"
    id: Optional[str] = None
    license: Optional[str] = None
    logo_url: Optional[str] = None


# ── Root model ─────────────────────────────────────────────────────────────────

class PropertyListing(BaseModel):
    # ── Identidad ─────────────────────────────────────────────────────────────
    url: str
    portal: str
    external_id: Optional[str] = None
    captured_at: Optional[datetime] = None

    # ── Secciones ─────────────────────────────────────────────────────────────
    listing: Optional[ListingInfo] = None
    price: Optional[PriceInfo] = None
    property_info: Optional[PropertyInfo] = Field(default=None, alias="property")
    location: Optional[LocationInfo] = None
    media: Optional[MediaInfo] = None
    seller: Optional[SellerInfo] = None

    model_config = {"populate_by_name": True}
