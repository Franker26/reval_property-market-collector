# property-market-collector — Claude Code

## Contexto del proyecto

Pipeline de market intelligence inmobiliario. Tres responsabilidades:

1. **Extracción por URL** (`POST /extract`) — dado una URL, extrae datos estructurados de la publicación
2. **Discovery pipeline** — descubre y trackea publicaciones de forma autónoma
3. **API de búsqueda de mercado** (`POST /market/facts/search`) — expone la capa analítica para `reval_acm_mi` (Odoo)

→ Arquitectura detallada en [docs/architecture.md](docs/architecture.md)
→ Convenciones en [docs/conventions.md](docs/conventions.md)

---

## Navegación del proyecto

```
main.py                          ← FastAPI app, lifespan, /health, /extract
app/
  core/
    config.py                    ← Settings (leer antes de agregar env vars — toda var nueva
                                    va en config.py + .env.example + docker-compose.yml)
    auth.py                      ← Dependency require_api_key (X-Reval-MI-Key)
    rate_limiter.py              ← RateLimiter adaptativo con cooldown
    log_buffer.py                ← Ring buffer de logs (GET /logs)
    logging_setup.py             ← Formato uniforme para root logger
    hashing.py                   ← SHA256 para change detection
    alerts.py                    ← Alertas Telegram (dispatch)
  db/
    models/
      portals.py                 ← MarketSource
      listings.py                ← ListingEntity, ListingSnapshot
      runs.py                    ← CollectionRun, CollectionError
      location_normalization.py  ← ListingLocationNormalization
      market_facts.py            ← ListingMarketFacts (fuente del endpoint de búsqueda)
      zonaprop/                  ← ZonapropSegment, ZonapropSegmentSnapshot, ScanQueue
    session.py                   ← get_async_session_factory(), get_db (FastAPI dep)
    seed.py                      ← seed de market_sources
  schemas/
    market.py                    ← MarketSearchRequest / MarketListingResult / MarketSearchResponse
  repositories/
    market_search.py             ← search_facts() — query del endpoint de mercado
    listings.py                  ← upsert_batch() (lógica A/B/C de lifecycle)
    market_facts.py              ← upsert_facts_batch()
    snapshots.py, sources.py, collection_runs.py, collection_errors.py, discovery_events.py
    zonaprop/                    ← segments.py, scan_queue.py
  routers/
    market.py                    ← POST /market/facts/search (API externa, auth por key)
    discovery.py                 ← POST /discovery/* (triggers manuales)
    ops.py                       ← GET /ops/dashboard, /ops/summary, POST /ops/cancel/*
    logs.py, runs.py, listings.py, errors.py, sources.py
  services/
    discovery_service.py         ← Orquesta las 3 fases del pipeline
    scheduler_service.py         ← APScheduler (3 jobs)
sources/
  base.py, models.py, _common.py, browser.py
  <portal>.py                    ← un archivo por portal (11 portales)
discovery/
  engine/                        ← engines genéricos (portal-agnostic)
  zonaprop/                      ← implementaciones específicas de Zonaprop
config/discovery/zonaprop.yaml   ← árboles de segmentación por operación/provincia/precio/superficie
jobs/                            ← scripts standalone para batch operations
```

---

## API de búsqueda de mercado

```
POST /market/facts/search
Header: X-Reval-MI-Key: <REVAL_MI_API_KEY>
```

La query une tres tablas: `listing_market_facts` (fuente principal) + `listing_entities` (url, title, rooms, bathrooms, garages) + `market_sources` (source name).

MI devuelve candidatos. ACM-MI (Odoo) calcula score. ACM decide. MI no hace scoring.

```bash
curl -X POST http://localhost:8200/market/facts/search \
  -H "Content-Type: application/json" \
  -H "X-Reval-MI-Key: $REVAL_MI_API_KEY" \
  -d '{
    "status": "active",
    "operation_type": "venta",
    "property_type": "departamentos",
    "neighborhood": "Caballito",
    "surface_total_min": 45,
    "surface_total_max": 85,
    "require_price": true,
    "require_surface": true,
    "min_data_quality_score": 75,
    "limit": 20
  }'
```

---

## Discovery pipeline (3 fases)

```
Fase 1 — segment_discovery (sábados 10:00 AR)
  └─ Árbol adaptativo precio × superficie por operación × provincia
  └─ Al finalizar: sync_pending_scan_queue() + invalidate_changed_segments_after_discovery()

Fase 2 — url_discovery_window (L-V 06:00-18:30 AR, domingos 10:00-16:00 AR)
  └─ Consume zonaprop_segment_scan_queue en estado pending
  └─ Pagina la API de Zonaprop por segmento → upsert en listing_entities
  └─ Resumable: runs colgados se devuelven a pending al arrancar

Fase 3 — incremental_monitor (bajo demanda)
  └─ Compara total_count actual vs snapshot anterior
  └─ Rescanea segmentos que cambiaron
```

---

## Cómo agregar un nuevo portal (extracción)

1. Crear `sources/<nombre>.py` extendiendo `BaseSource` (`sources/base.py`)
2. Implementar `can_handle(url)` y `extract(url, client) → PropertyListing`
3. Registrar en `sources/__init__.py`
4. Los portales SPA usan `browser_page()` de `browser.py`; los server-rendered usan `fetch_html()` de `_common.py`

---

## Restricciones importantes

- **Todos los campos de `PropertyListing` son opcionales** — `None` si el portal no publica el dato
- **Playwright es singleton** — nunca instanciar `Browser` directamente, siempre `browser_page()`
- **`upsert_segment()` es idempotente** por `uq_zonaprop_segments_boundaries`
- **Toda env var nueva** va en tres lugares: `config.py`, `.env.example`, `docker-compose.yml` (sección `environment:`)
- **MI no hace scoring** — la lógica de comparabilidad vive en `reval_acm_mi` (Odoo)
- El servicio corre en Docker; no asumir paths locales absolutos

---

## Comandos útiles

```bash
# Levantar
docker compose up --build

# Health
curl http://localhost:8200/health

# Búsqueda de mercado (sin key en desarrollo)
curl -s -X POST http://localhost:8200/market/facts/search \
  -H "Content-Type: application/json" -d '{"limit": 5}' | python3 -m json.tool

# Discovery manual
curl -X POST http://localhost:8200/discovery/segment-discovery
curl -X POST http://localhost:8200/discovery/url-discovery

# Logs
curl "http://localhost:8200/logs?logger=scheduler&level=INFO"
```

Para pruebas de endpoints: colección Bruno en `bruno/` — carpetas `extract/` y `market/`.

---

## Tests y validación

No hay suite de tests automatizada. Para validar cambios:
1. `docker compose up`
2. Colección Bruno en `bruno/`
3. `/health`, `/extract`, `/market/facts/search` como smoke tests
