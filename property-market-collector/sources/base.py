from abc import ABC, abstractmethod
from typing import Optional

import httpx


class BaseSource(ABC):
    @staticmethod
    @abstractmethod
    def can_handle(url: str) -> bool: ...

    @abstractmethod
    async def extract(self, url: str, client: httpx.AsyncClient) -> dict: ...

    @abstractmethod
    async def search_listings(
        self,
        operacion: str,
        tipo: str,
        ubicacion: str,
        precio_min: Optional[int],
        precio_max: Optional[int],
        ambientes_min: Optional[int],
        ambientes_max: Optional[int],
        superficie_min: Optional[int],
        superficie_max: Optional[int],
        paginas: int,
        client: httpx.AsyncClient,
    ) -> list[str]:
        """Retorna lista de URLs de publicaciones individuales que coinciden con la búsqueda."""
        ...
