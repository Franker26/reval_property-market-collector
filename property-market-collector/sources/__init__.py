import httpx
from fastapi import HTTPException

from .argenprop import ArgenpropSource
from .mercadolibre import MercadoLibreSource
from .zonaprop import ZonapropSource
from .lacapital import LacapitalSource
from .propia import PropiaSource
from .liderprop import LiderpropSource
from .inmoclick import InmoclickSource
from .buscadorprop import BuscadorpropSource
from .buscainmueble import BuscainmuebleSource
from .clarin import ClarinSource
from .doomos import DoomosSource
_SOURCES = [
    ZonapropSource(),
    ArgenpropSource(),
    MercadoLibreSource(),
    LacapitalSource(),
    PropiaSource(),
    LiderpropSource(),
    InmoclickSource(),
    BuscadorpropSource(),
    BuscainmuebleSource(),
    ClarinSource(),
    DoomosSource(),
]

_SOURCE_NAMES = [
    "zonaprop",
    "argenprop",
    "mercadolibre",
    "lacapital",
    "propia",
    "liderprop",
    "inmoclick",
    "buscadorprop",
    "buscainmueble",
    "clarin",
    "doomos",
]

SUPPORTED_SOURCES = _SOURCE_NAMES


async def extract(url: str, client: httpx.AsyncClient) -> dict:
    for source in _SOURCES:
        if source.can_handle(url):
            return await source.extract(url, client)
    raise HTTPException(400, "No hay soporte de scraping para esta URL.")
