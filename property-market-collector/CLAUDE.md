# property-market-collector — Claude Code

## Contexto del proyecto

Pipeline de market intelligence inmobiliario. Acumula snapshots históricos de propiedades para análisis. Tiene dos responsabilidades principales:

1. **Extracción por URL** (`POST /extract`) — dado una URL, extrae datos estructurados de la publicación
2. **Discovery pipeline** — descubre y trackea publicaciones de forma autónoma, sin que el consumidor provea URLs

→ Arquitectura detallada en [docs/architecture.md](docs/architecture.md)
→ Convenciones en [docs/conventions.md](docs/conventions.md)

---

## Navegación del proyecto

```
main.py                          ← FastAPI app, lifespan, /health, /extract
app/
  core/
    config.py                    ← Settings (leer antes de agregar env vars)
    rate_limiter.py              ← RateLimiter adaptativo con cooldown
    log_buffer.py                ← Ring buffer de logs (GET /logs)
    logging_setup.py             ← Formato uniforme para root logger
    hashing.py                   ← SHA256 para change detection
  db/
    models.py                    ← SQLAlchemy ORM (MarketSegment, ListingEntity, etc.)
    session.py                   ← get_async_session_factory()
    seed.py                      ← seed de market_sources
  repositories/
    market_segments.py           ← upsert_segment(), sync_pending_segment_runs()
    listings.py                  ← upsert_batch() (lógica A/B/C/D de lifecycle)
    url_discovery_segment_runs.py← progress tracking de url_discovery por segmento
    snapshots.py, sources.py, collection_runs.py, collection_errors.py, discovery_events.py
  routers/
    discovery.py                 ← POST /discovery/* (triggers manuales)
    logs.py                      ← GET /logs (ring buffer)
    listings.py, runs.py, errors.py, sources.py
  services/
    discovery_service.py         ← Orquesta las 3 fases del pipeline
    scheduler_service.py         ← APScheduler (segment_discovery sábados, url_discovery L-V)
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

## Discovery pipeline (3 fases)

```
Fase 1 — segment_discovery (sábados 10:00 AR, 4h ventana)
  └─ Construye árbol adaptativo precio × superficie por operación × provincia
  └─ Persiste en market_segments con UniqueConstraint por boundaries
  └─ Al finalizar: sync_pending_segment_runs() crea runs para leaf segments

Fase 2 — url_discovery_window (L-V 06:00-18:30/19:00 AR)
  └─ Consume url_discovery_segment_runs en estado pending
  └─ Pagina la API de Zonaprop por segmento → upsert en listing_entities
  └─ Resumable: cada run guarda progreso, los colgados se recuperan

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
- **`upsert_segment()` es idempotente** por `uq_market_segments_boundaries` — el mismo segmento siempre tiene el mismo ID
- El servicio corre en Docker; no asumir paths locales absolutos

---

## Comandos útiles

```bash
# Levantar
docker compose up --build

# Health + logs
curl http://localhost:8200/health
curl http://localhost:8200/logs
curl "http://localhost:8200/logs?logger=scheduler&level=INFO"

# Disparar discovery manualmente
curl -X POST http://localhost:8200/discovery/segment-discovery
curl -X POST http://localhost:8200/discovery/url-discovery

# Progreso de url_discovery
# SELECT status, COUNT(*) FROM url_discovery_segment_runs GROUP BY status;
```

Para pruebas de endpoints: colección Bruno en `bruno/` contra `http://localhost:8200`.

---

## Tests y validación

No hay suite de tests automatizada. Para validar cambios:
1. `docker compose up`
2. Colección Bruno en `bruno/`
3. `/health`, `/extract`, `/logs` como smoke test
