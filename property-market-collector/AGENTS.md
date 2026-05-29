# property-market-collector — Codex / Agent Instructions

## Contexto del proyecto

Pipeline de market intelligence inmobiliario (FastAPI + asyncio + PostgreSQL). Tiene dos responsabilidades:

1. **Extracción por URL** (`POST /extract`) — dado una URL, extrae datos estructurados de la publicación
2. **Discovery autónomo** — descubre y trackea publicaciones de forma autónoma a través de un árbol de segmentos precio × superficie

**No implementa búsqueda.** El discovery pipeline descubre URLs directamente desde la API del portal.

→ Arquitectura detallada: [docs/architecture.md](docs/architecture.md)
→ Convenciones: [docs/conventions.md](docs/conventions.md)

---

## Estructura crítica

```
main.py                          ← FastAPI app, /health, /extract, /logs
app/core/config.py               ← Settings (env vars centralizadas)
app/db/models.py                 ← ORM: MarketSegment, ListingEntity, UrlDiscoverySegmentRun, etc.
app/repositories/                ← Data access layer (un módulo por entidad)
app/services/discovery_service.py← Orquesta el pipeline de discovery
app/services/scheduler_service.py← APScheduler (2 jobs: sábados + L-V)
sources/base.py                  ← BaseSource (leer antes de tocar portales)
sources/models.py                ← PropertyListing
sources/_common.py               ← fetch_html, parse_ldjson
sources/browser.py               ← singleton Playwright, usar browser_page()
discovery/engine/                ← engines genéricos portal-agnostic
discovery/zonaprop/              ← implementaciones específicas de Zonaprop
```

## Reglas para modificar código

1. **No cambiar la firma de `BaseSource`** sin actualizar todos los portales.
2. **No instanciar `async_playwright()` directamente** — usar `browser_page()` de `browser.py`.
3. **Todos los campos de `PropertyListing` son opcionales** — retornar `None` cuando el dato no está disponible.
4. **`upsert_segment()` es idempotente** por `uq_market_segments_boundaries` — nunca hacer INSERT directo a `market_segments`.
5. No agregar dependencias sin actualizar `requirements.txt`.
6. Nuevas env vars van en `app/core/config.py` (Settings) y en `.env.example`.

## Flujo de extracción

```
POST /extract
  └─ sources.extract(url, client)
        └─ source = next(s for s in SOURCES if s.can_handle(url))
              └─ source.extract(url, client) → PropertyListing
```

## Flujo de discovery

```
scheduler → run_segment_discovery() [sábados]
  └─ Árbol adaptativo → market_segments (upsert por boundaries)
  └─ sync_pending_segment_runs() → url_discovery_segment_runs

scheduler → run_url_discovery_window(stop_at) [L-V]
  └─ Consume runs pending → pagina API → upsert listing_entities
```

## Comandos de verificación

```bash
docker compose up --build
curl http://localhost:8200/health
curl http://localhost:8200/logs
curl -X POST http://localhost:8200/discovery/segment-discovery
```

Para probar endpoints completos usar la colección Bruno en `bruno/`.
