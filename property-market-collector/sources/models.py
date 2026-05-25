from typing import Optional
from pydantic import BaseModel


class PropertyListing(BaseModel):
    url: str
    portal: str
    imagen_url: Optional[str] = None
    precio: Optional[int] = None
    direccion: Optional[str] = None
    tipo: Optional[str] = None
    ambientes: Optional[int] = None          # cantidad de ambientes (1 = monoambiente)
    dias_mercado: Optional[int] = None
    superficie_total: Optional[float] = None
    superficie_cubierta: Optional[float] = None
    superficie_semicubierta: Optional[float] = None
    superficie_descubierta: Optional[float] = None
    antiguedad: Optional[int] = None
    orientacion: Optional[str] = None
    piso: Optional[int] = None
    cochera: Optional[bool] = None
    pileta: Optional[bool] = None
