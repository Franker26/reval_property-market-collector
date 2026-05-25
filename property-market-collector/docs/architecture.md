# Arquitectura — property-market-collector

## Vista general

El servicio recibe una URL de publicación inmobiliaria y devuelve los datos estructurados de esa publicación. No realiza búsquedas: la responsabilidad de proveer URLs está en el sistema que lo consume.

```
POST /extract
  └─ sources.extract(url, client)
        └─ [source for source in SOURCES if source.can_handle(url)][0]
              └─ source.extract(url, client) → PropertyListing
```

## Capas

### 1. Entrypoint — `main.py`

- Define la app FastAPI y los endpoints `/health` y `/extract`.
- Instancia todos los portales al arrancar.
- Mantiene el **singleton de Playwright** vía el lifespan de FastAPI.

### 2. Capa de abstracción — `sources/base.py`

`BaseSource` define el contrato mínimo de cada portal:

```python
class BaseSource(ABC):
    name: str                          # slug del portal (ej: "zonaprop")
    base_url: str

    @staticmethod
    def can_handle(url: str) -> bool: ...     # detecta si la URL pertenece a este portal

    async def extract(self, url: str, client: httpx.AsyncClient) -> PropertyListing: ...
```

### 3. Modelo de datos — `sources/models.py`

- `PropertyListing`: único modelo de datos, con todos los campos opcionales (`None` si el portal no publica el dato).

### 4. Utilidades compartidas — `sources/_common.py`

| Función              | Uso                                                              |
|----------------------|------------------------------------------------------------------|
| `fetch_html(url)`    | httpx async, User-Agent rotado, fallback a Playwright en 403    |
| `parse_ldjson(html)` | extrae y parsea bloques `<script type="application/ld+json">`   |
| `slugify(s)`         | normalización de strings para URLs                               |

### 5. Playwright singleton — `sources/browser.py`

- Mantiene una instancia única de `Browser` (Chromium headless) durante toda la vida del proceso.
- Expone `browser_page()` como context manager: abre una `Page`, la devuelve, la cierra al salir.
- Los portales SPA (MercadoLibre, Doomos) usan `browser_page()` para extraer.

### 6. Portales — `sources/<portal>.py`

Cada archivo implementa `BaseSource`. Patrones existentes:

| Tecnología                       | Portales                                                              |
|----------------------------------|-----------------------------------------------------------------------|
| httpx + JSON state (JS embebido) | ZonaProp                                                              |
| httpx + JSON-LD                  | Argenprop, Propia, LiderProp, Inmoclick, BuscadorProp, BuscaInmueble, Clarín |
| httpx + HTML (BeautifulSoup)     | La Capital                                                            |
| Playwright (SPA)                 | MercadoLibre, Doomos                                                  |

---

## Decisiones de diseño

**¿Por qué extracción por URL y no búsqueda?**
Este servicio es un componente de captura puntual. El sistema de market intelligence que lo consume es el responsable de mantener el catálogo de URLs a trackear y de programar la frecuencia de snapshots. Separar esas responsabilidades hace al servicio más simple, stateless y fácil de escalar.

**¿Por qué Playwright es singleton?**
Playwright maneja un pool de páginas limitado. Instanciar un browser por request sería prohibitivo en memoria y tiempo de arranque. El singleton comparte la instancia entre todos los requests.
