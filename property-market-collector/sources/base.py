from abc import ABC, abstractmethod

import httpx


class BaseSource(ABC):
    @staticmethod
    @abstractmethod
    def can_handle(url: str) -> bool: ...

    @abstractmethod
    async def extract(self, url: str, client: httpx.AsyncClient) -> dict: ...
