# Arquitectura — property-market-collector

## Vista general

Pipeline de market intelligence inmobiliario con tres responsabilidades:

1. **Extracción puntual** (`POST /extract`) — dado una URL, extrae datos estructurados de la publicación.
2. **Discovery autónomo** — descubre y trackea publicaciones sin que el consumidor provea URLs.
3. **API de búsqueda de mercado** (`POST /market/facts/search`) — expone la capa analítica para consumo externo.

El discovery opera en tres fases secuenciales sobre Zonaprop. Las fuentes de extracción puntual cubren 11 portales.

---

## Estructura del proyecto

```
main.py                          ← FastAPI app, lifespan, /health, /extract
app/
  core/
    config.py                    ← Settings (env vars via os.getenv, singleton lru_cache)
    auth.py                      ← Dependency require_api_key (X-Reval-MI-Key)
    rate_limiter.py              ← RateLimiter adaptativo con cooldown
    log_buffer.py                ← Ring buffer de logs (GET /logs)
    logging_setup.py             ← Formato uniforme para root logger
    hashing.py                   ← SHA256 para change detection
    alerts.py                    ← Alertas Telegram (dispatch)
  db/
    models/
      base.py                    ← DeclarativeBase
      portals.py                 ← MarketSource
      listings.py                ← ListingEntity, ListingSnapshot
      runs.py                    ← CollectionRun, CollectionError
      events.py                  ← DiscoveryEvent
      location_normalization.py  ← ListingLocationNormalization
      market_facts.py            ← ListingMarketFacts
      zonaprop/
        segments.py              ← ZonapropSegment, ZonapropSegmentSnapshot
        scan_queue.py            ← ZonapropSegmentScanQueue
    session.py                   ← get_async_session_factory(), get_db (FastAPI dep)
    seed.py                      ← seed de market_sources
  schemas/
    market.py                    ← MarketSearchRequest, MarketListingResult, MarketSearchResponse
  repositories/
    market_search.py             ← search_facts() — query principal del endpoint de mercado
    zonaprop/
      segments.py                ← upsert_segment, sync_pending_scan_queue,
                                    invalidate_changed_segments_after_discovery,
                                    save_snapshot, deactivate_portal_segments
      scan_queue.py              ← get_pending, mark_started/complete/failed/pending,
                                    reset_stale_running
    listings.py                  ← upsert_batch (lógica A/B/C de lifecycle)
    snapshots.py, sources.py, collection_runs.py, collection_errors.py
  services/
    discovery_service.py         ← Orquesta las 3 fases + post-discovery invalidation
    scheduler_service.py         ← APScheduler (3 jobs)
  routers/
    market.py                    ← POST /market/facts/search (API externa)
    discovery.py                 ← POST /discovery/* (triggers manuales)
    ops.py                       ← GET /ops/dashboard, /ops/summary, POST /ops/cancel/*
    logs.py, runs.py, listings.py, errors.py, sources.py
discovery/
  engine/
    segment_discovery.py         ← Algoritmo adaptativo genérico (portal-agnostic)
  zonaprop/
    adapter.py                   ← Payload builder + extracción de postings
    segment_config.py            ← load_config() desde zonaprop.yaml
    segment_discovery.py         ← Wrapper Zonaprop del engine
    url_discovery.py             ← Paginación y persistencia de URLs por segmento
    incremental_monitor.py       ← Comparación total_count vs snapshot anterior
sources/
  base.py, models.py, _common.py, browser.py
  <portal>.py                    ← Un archivo por portal (11 portales)
config/discovery/zonaprop.yaml   ← Parámetros del árbol adaptativo
jobs/                            ← Scripts standalone para operaciones batch
```

---

## Schema de base de datos

**Tablas genéricas:**

| Tabla | Propósito |
|---|---|
| `market_sources` | Registro de portales (zonaprop, argenprop, etc.) |
| `listing_entities` | Estado actual de cada publicación (mutable) |
| `listing_snapshots` | Historial de cambios (append-only) |
| `collection_runs` | Trazabilidad de cada ejecución del pipeline |
| `collection_errors` | Errores por run (HTTP, parsing, etc.) |
| `discovery_events` | Eventos de observabilidad granular |

**Tablas Zonaprop:**

| Tabla | Propósito |
|---|---|
| `zonaprop_segments` | Árbol adaptativo precio × superficie |
| `zonaprop_segment_snapshots` | Historial de total_count por segmento |
| `zonaprop_segment_scan_queue` | Cola de escaneo de URLs por segmento |

**Capa analítica:**

| Tabla | Propósito |
|---|---|
| `listing_location_normalization` | Ubicación cruda + normalizada por publicación |
| `listing_market_facts` | Métricas pre-calculadas por publicación (fuente del endpoint de búsqueda) |

Schema creado automáticamente en el arranque via `Base.metadata.create_all()`. Columnas en tablas existentes: migraciones SQL manuales en `migrations/`.

---

## Pipeline de discovery — 3 fases

### Fase 1 — segment_discovery (sábados 10:00 AR)

Construye el árbol adaptativo precio × superficie por cada combinación operación × provincia.

**Algoritmo** (`discovery/engine/segment_discovery.py`):
1. Para cada raíz (operación, provincia): consulta API → obtiene total_count.
2. Si count ≤ `max_results_per_segment` (2000) → hoja.
3. Si count > umbral y depth < max_depth → divide por precio (si ancho > 10k USD) o superficie (si ancho > 10 m²).
4. Al llegar a max_depth con count > umbral → hoja oversized.

**Persistencia**: upsert idempotente por `uq_zonaprop_segments_boundaries`. Segmentos existentes se reactivan; nuevos se crean.

**Al finalizar**:
1. `sync_pending_scan_queue` → inserta entradas faltantes en la cola.
2. `invalidate_changed_segments_after_discovery` → reinvalida entradas `complete` a `pending` si el total_count cambió significativamente.

### Fase 2 — url_discovery_window (L-V 06:00-18:30 AR, domingos 10:00-16:00 AR)

Consume `zonaprop_segment_scan_queue` en estado `pending`.

- **Resumable**: runs colgados (>6h) se devuelven a `pending` al inicio de cada ventana.
- **Ciclo por segmento**: `pending` → `running` → `complete` | `failed` (máx 3 intentos).
- **Persist callback**: por cada página, upsert batch en `listing_entities` + `listing_snapshots`.

### Fase 3 — incremental_monitor (bajo demanda)

Consulta el total_count actual de cada segmento activo y lo compara con el snapshot anterior. Rescanea los que cambiaron sin reconstruir el árbol.

---

## Lifecycle de listings

| Caso | Acción |
|---|---|
| A — nuevo | INSERT entity + INSERT snapshot |
| B — sin cambios | UPDATE `last_seen_at` en entity |
| C — cambió hash | UPDATE entity con nuevo estado + INSERT snapshot |

`listing_entities` siempre tiene el estado más reciente. `listing_snapshots` es append-only.

---

## Capa analítica

### listing_location_normalization

Separa la ubicación cruda del portal de la geolocalización normalizada/validada.

- lat/lon presente → `geo_status='coordinates'`, copiados como normalized.
- sin coordenadas → `geo_status='pending'` para geocoding futuro.
- Actualizada por `jobs/build_location_normalization.py`.

### listing_market_facts

Métricas pre-calculadas para que la API de búsqueda sea eficiente.

- Fuente principal de `POST /market/facts/search`.
- `price_usd`, `price_per_m2_*`, historial de precios, `data_quality_score` (0/25/50/75/100), `market_bucket`.
- Ubicación desde `listing_location_normalization` si existe; fallback a raw de entity.
- Actualizada por `jobs/build_market_facts.py` (incremental o full).

---

## API de búsqueda de mercado

### Endpoint

```
POST /market/facts/search
Header: X-Reval-MI-Key: <REVAL_MI_API_KEY>
```

### Propósito

Expone `listing_market_facts` como API neutral de candidatos de mercado.

```
MI devuelve candidatos filtrables.
reval_acm_mi (Odoo) calcula score de comparabilidad.
reval_acm_integrations extrae live la URL aceptada.
reval_acm decide el comparable.
```

MI no sabe qué es un comparable. No hace scoring, no rankea por similitud, no selecciona automáticamente.

### Componentes

| Archivo | Rol |
|---|---|
| `app/schemas/market.py` | Contrato Pydantic de request y response |
| `app/repositories/market_search.py` | Query con JOIN a `listing_entities` y `market_sources` |
| `app/routers/market.py` | FastAPI router + serialización |
| `app/core/auth.py` | Dependency `require_api_key` |

### Query

La query principal une tres tablas:
- `listing_market_facts` — datos analíticos (fuente principal)
- `listing_entities` — `canonical_url`, `generated_title`, `rooms`, `bedrooms`, `bathrooms`, `garages` (no están en market_facts)
- `market_sources` (LEFT JOIN) — `code` para el campo `source` en la response

### Autenticación

`REVAL_MI_API_KEY` en `.env` y en `docker-compose.yml` (variable `${REVAL_MI_API_KEY:-}`).

- Sin key configurada + `APP_ENV=development` → permite acceso (conveniencia local).
- Sin key configurada + `APP_ENV=production` → 403.
- Key incorrecta → 401.

### Filtros

Todos opcionales. Los flags `require_price/require_surface/require_location` solo aplican condición `IS NOT NULL` si vienen `true` — no se hardcodean como condición global.

---

## Extracción puntual (`POST /extract`)

```
POST /extract
  └─ sources.extract(url, client)
        └─ [source for source in SOURCES if source.can_handle(url)][0]
              └─ source.extract(url, client) → PropertyListing
```

| Tecnología | Portales |
|---|---|
| curl_cffi + JSON (JS embebido) | Zonaprop |
| httpx + JSON-LD | Argenprop, Propia, LiderProp, Inmoclick, BuscadorProp, BuscaInmueble, Clarín |
| httpx + HTML (BeautifulSoup) | La Capital |
| Playwright (SPA) | MercadoLibre, Doomos |

---

## Scheduler

| Job | Trigger |
|---|---|
| `weekly_segment_discovery` | Sábados 10:00 AR |
| `weekday_url_discovery` | L-V 06:00 AR, corta 18:30-19:00 AR |
| `sunday_url_discovery` | Domingos 10:00 AR, corta 16:00-16:30 AR |

---

## Observabilidad

- `GET /ops/dashboard` — estado de la queue, último run, métricas agregadas.
- `GET /logs?logger=&level=` — ring buffer de logs en memoria.
- Alertas Telegram: `run_started`, `run_completed`, `run_failed`, `error_rate_exceeded`.
- Cancelación graceful: `POST /ops/cancel/{segment_discovery|url_discovery|incremental_monitor}`.
