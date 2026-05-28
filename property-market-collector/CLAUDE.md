# scraper-service — Claude Code

## Contexto del proyecto

Microservicio de extracción de publicaciones inmobiliarias a partir de URLs. Recibe una URL de un portal soportado y devuelve los datos estructurados de esa publicación como snapshot. No realiza búsquedas; el sistema externo es responsable de proveer las URLs a extraer.

Es el componente de captura del **property-market-collector**: un virtual cellar de propiedades que acumula snapshots históricos para market intelligence.

→ Ver arquitectura completa en [docs/architecture.md](docs/architecture.md)
→ Convenciones de código en [docs/conventions.md](docs/conventions.md)

## Navegación del proyecto

Este proyecto tiene **codegraph** configurado en `.codegraph/`. Usalo para saltar entre símbolos y entender dependencias antes de editar.

Símbolos clave:
- `BaseSource` — punto de extensión para nuevos portales
- `PropertyListing` — modelo de datos
- `fetch_html` — acceso HTTP con fallback a Playwright
- `browser_page` — acceso al singleton de Playwright

Entrypoint: `main.py` · Portales: `sources/<portal>.py` · Utilidades: `sources/_common.py`, `sources/browser.py`

---

## Cómo agregar un nuevo portal

1. Crear `sources/<nombre>.py` que extienda `BaseSource` (`sources/base.py`)
2. Implementar `can_handle(url)` → `bool` (detecta si la URL pertenece a este portal)
3. Implementar `extract(url, client)` → `PropertyListing`
4. Registrar en `sources/__init__.py` y en la lista `SOURCES` de `main.py`
5. Agregar el portal a la tabla de portales del `README.md`

Los portales SPA (JS pesado) deben usar `browser_page()` de `browser.py`. Los server-rendered usan `fetch_html()` de `_common.py` con fallback automático a Playwright si reciben 403.

---

## Restricciones importantes

- **No romper el contrato de `PropertyListing`** — todos los campos son opcionales (`None` si el portal no lo publica), pero los tipos deben respetarse.
- **Playwright es singleton** (`browser.py`): no instanciar `Browser` directamente, siempre usar `browser_page()`.
- **No agregar endpoints de búsqueda** — este servicio opera exclusivamente por URL. La lógica de descubrimiento de URLs es responsabilidad del sistema consumidor.
- El servicio corre en Docker; no asumir paths locales absolutos.

---

## Comandos útiles

```bash
# Levantar el servicio
docker compose up --build

# Smoke test
curl http://localhost:8200/health

# Probar extracción
curl -X POST http://localhost:8200/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.zonaprop.com.ar/propiedades/..."}'
```

Para pruebas completas: colección Bruno en `bruno/` contra `http://localhost:8200`.

---

## Tests y validación

No hay suite de tests automatizada. Para validar cambios:
1. Levantar con `docker compose up`
2. Usar la colección Bruno en `bruno/` contra `http://localhost:8200`
3. Verificar que `/health` y `/extract` respondan correctamente para el portal modificado
