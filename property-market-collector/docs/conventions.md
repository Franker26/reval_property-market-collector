# Convenciones — property-market-collector

## Python

- **Python 3.12+**, async/await en todo el código de I/O.
- Type hints obligatorios en funciones públicas de `base.py` y `models.py`.
- Pydantic v2 para modelos (`PropertyListing`, `ZonapropListing`).
- Sin frameworks de testing aún — validar manualmente con Bruno.

## Portales

- Un archivo por portal en `sources/`, nombrado igual que el slug del portal (ej: `sources/zonaprop.py`).
- El atributo `name` de la clase debe coincidir con el slug registrado en `sources/__init__.py`.
- `can_handle(url)` debe ser una función pura y rápida — solo inspecciona el dominio, no hace I/O.
- Nunca capturar excepciones silenciosamente dentro de `extract()`: dejar que suban para que el caller las maneje.
- Los campos `None` en `PropertyListing` son esperados — no defaultear a `0` o `""`.

## Scraping

- Preferir `fetch_html()` de `_common.py` sobre instanciar `httpx.AsyncClient` directamente.
- Usar `parse_ldjson()` para extraer JSON-LD antes de parsear HTML crudo.
- Para Playwright, siempre usar el context manager `browser_page()` — no retener referencias a `Page` entre requests.
- User-Agent debe venir del pool rotado en `_common.py`, no hardcodearse.

## Codegraph

El proyecto tiene `.codegraph/` con el índice de símbolos. Está configurado para indexar todos los `.py`. Cuando la base de código crece, regenerar el índice con el CLI de codegraph para mantenerlo útil.

Símbolos centrales que conviene tener indexados:
- `BaseSource` — punto de extensión principal
- `PropertyListing` — contrato de datos canónico
- `browser_page` — acceso a Playwright
- `fetch_html` — acceso HTTP

## Git

- Commits en español, formato: `tipo(scope): descripción`
- Tipos: `feat`, `fix`, `refactor`, `docs`, `chore`
- Ejemplo: `feat(scraper): agregar soporte para portal remax`
