# Convenciones — property-market-collector

## Python

- **Python 3.12+**, async/await en todo el código de I/O.
- Type hints obligatorios en funciones públicas.
- Pydantic v2 para modelos de datos (`PropertyListing`, schemas de API en `app/schemas/`).
- Sin suite de tests automatizada — validar con Bruno + smoke tests manuales.

## Git

- Commits en español, formato: `tipo(scope): descripción`
- Tipos: `feat`, `fix`, `refactor`, `docs`, `chore`
- Ejemplo: `feat(market): endpoint POST /market/facts/search para reval_acm_mi`

---

## SQLAlchemy

- Usar `case((condición, valor), else_=default)` para expresiones condicionales en SQL. `func.cast(type_=None)` no existe.
- En `group_by`, pasar la expresión directamente (no `text("alias")`): `group_by(ZonapropSegment.status)`, no `group_by(text("status"))`.
- Los updates con `session.execute(update(...).values(...))` **no disparan** `onupdate`. Siempre incluir `updated_at=datetime.now(timezone.utc)` explícitamente en los `.values()`.
- Para window functions: `func.row_number().over(partition_by=..., order_by=...)`.
- En queries multi-tabla (SELECT con JOIN), `result.all()` devuelve `Row` donde el modelo ORM se accede por nombre de clase: `row.ListingMarketFacts`, `row.canonical_url`.

## Schema

- Schema creado automáticamente al arrancar via `Base.metadata.create_all()`.
- Para tablas que ya existen en producción, agregar columnas nuevas con `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. No crear scripts de migración para DBs vacías.
- Columnas nuevas opcionales (`Mapped[Optional[...]]`) no requieren default en la DB — se agregan como nullable sin bloquear la tabla.

## Repositorios

- Cada función del repositorio recibe `session: AsyncSession` ya abierta — no abre ni commitea transacciones propias.
- Las funciones que modifican datos se llaman dentro de `async with session.begin()` en el caller (service o job).
- Nombres: `upsert_*`, `get_*`, `mark_*`, `sync_*`, `deactivate_*`, `invalidate_*`, `search_*`.
- Los repositorios de búsqueda (read-only) devuelven `tuple[int, list[Row]]` — count total + resultados paginados.

## Schemas de API (`app/schemas/`)

- Un archivo por dominio funcional (ej: `market.py`).
- Los schemas de request usan `@field_validator` para whitelists (ej: `sort_by`). No validar en el router.
- Las constraints de rango van en `Field(..., ge=..., le=...)`.
- Los flags opt-in (`require_price`, `require_surface`, `require_location`) son `Optional[bool] = None`. Solo aplican condición en la query si vienen `True` — nunca hardcodear NULLs globales.

## Autenticación

- `app/core/auth.py` — dependency `require_api_key` para endpoints externos.
- Header `X-Reval-MI-Key` → variable Python `x_reval_mi_key` (FastAPI normaliza hyphens a underscores).
- Sin key en `.env` + `APP_ENV=development` → permite acceso. Sin key + `APP_ENV=production` → 403.
- Toda variable nueva de env va en: `app/core/config.py` (class + `__init__`), `.env.example`, `docker-compose.yml` (sección `environment:` con `${VAR:-}`). Los tres lugares.

## Discovery pipeline

- El engine `discovery/engine/` es portal-agnostic. La lógica específica de Zonaprop vive en `discovery/zonaprop/`.
- Los callbacks (`on_leaf_found`, `persist_fn`, `error_fn`) permiten inyectar persistencia sin acoplar el engine a SQLAlchemy.
- `upsert_segment` es idempotente por `uq_zonaprop_segments_boundaries` — el mismo segmento siempre tiene el mismo ID.
- El orden de operaciones al finalizar segment_discovery es invariante: (1) `sync_pending_scan_queue`, (2) `invalidate_changed_segments_after_discovery`. Nunca invertir.

## Portales (extracción puntual)

- Un archivo por portal en `sources/`, nombrado igual que el slug del portal.
- `can_handle(url)` es una función pura y rápida — solo inspecciona el dominio, sin I/O.
- No capturar excepciones silenciosamente dentro de `extract()`.
- Los campos `None` en `PropertyListing` son esperados — no defaultear a `0` o `""`.
- Playwright es singleton: siempre usar el context manager `browser_page()`, nunca retener referencias a `Page`.
- `moneda: "2"` = USD en el payload de Zonaprop (crítico para filtros de precio correctos).

## Alertas

- Toda alerta pasa por `app.core.alerts.dispatch(event_type, level, message, context_dict)`.
- No llamar a Telegram directamente desde repositorios ni desde el engine.

## Separación de responsabilidades

MI no calcula score de comparables, no rankea por similitud, no selecciona automáticamente.
Esa lógica vive en `reval_acm_mi` (Odoo).
`POST /market/facts/search` devuelve candidatos filtrables, nada más.
