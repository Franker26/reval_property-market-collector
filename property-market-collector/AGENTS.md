# property-market-collector — Codex / Agent Instructions

## Contexto del proyecto

Microservicio Python (FastAPI + asyncio) que extrae datos estructurados de publicaciones inmobiliarias a partir de URLs. Soporta 11 portales argentinos. Expone un único endpoint de extracción: `POST /extract`.

**No implementa búsqueda.** Las URLs a extraer las provee el sistema consumidor. Este servicio es el componente de captura de un sistema de market intelligence que acumula snapshots históricos de propiedades.

→ Arquitectura detallada: [docs/architecture.md](docs/architecture.md)
→ Convenciones: [docs/conventions.md](docs/conventions.md)

---

## Estructura crítica

```
main.py                  ← FastAPI app, endpoints, lifespan (Playwright)
sources/
  base.py                ← clase abstracta BaseSource (LEER ANTES de tocar portales)
  models.py              ← PropertyListing
  _common.py             ← fetch_html, parse_ldjson, slugify
  browser.py             ← singleton Playwright, usar browser_page()
  <portal>.py            ← un archivo por portal
```

## Reglas para modificar código

1. **No cambiar la firma de `BaseSource`** sin actualizar todos los portales que la implementan.
2. **No instanciar `async_playwright()` directamente** — usar `browser_page()` de `browser.py`.
3. **Todos los campos de `PropertyListing` son opcionales** — retornar `None` cuando el dato no está disponible, nunca lanzar excepción por dato faltante.
4. **No agregar endpoints de búsqueda ni endpoints específicos por portal** — el servicio expone únicamente `POST /extract`.
5. No agregar dependencias sin actualizar `requirements.txt`.

## Navegación con codegraph

El proyecto tiene codegraph indexado en `.codegraph/`. Si tu entorno lo soporta, usalo para:
- Encontrar dónde se define un símbolo (`BaseSource`, `PropertyListing`, etc.)
- Ver qué archivos llaman a `fetch_html` o `browser_page`
- Navegar el grafo de llamadas de un portal específico

---

## Flujo de extracción

```
POST /extract
  └─ sources.extract(url, client)
        └─ source = next(s for s in SOURCES if s.can_handle(url))
              └─ source.extract(url, client) → PropertyListing
```

## Comandos de verificación

```bash
docker compose up --build          # levantar
curl http://localhost:8100/health  # smoke test
```

Para probar endpoints completos usar la colección Bruno en `bruno/`.
