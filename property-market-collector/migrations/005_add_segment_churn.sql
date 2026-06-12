-- Refresh rotativo Etapa B: churn observado por segmento.
--
-- 1. Columnas de churn en zonaprop_segments: señal principal del score v2.
--    churn_ewma NULL + churn_samples_count=0 => el segmento se clasifica como
--    tier 'unknown' (exploración), nunca directamente como cold.
-- 2. zonaprop_segment_scan_history: registro append-only de cada scan completado.
--    Sirve para auditoría operativa, calibración del refresh, debugging y
--    dataset futuro de ML. Es también el estado durable de los batches de
--    full scan (idempotencia por batch_id + scan_mode).
--
-- Aplicar manualmente antes de desplegar Etapa B. create_all cubre instalaciones frescas.

ALTER TABLE zonaprop_segments
    ADD COLUMN IF NOT EXISTS churn_last NUMERIC,
    ADD COLUMN IF NOT EXISTS churn_ewma NUMERIC,
    ADD COLUMN IF NOT EXISTS churn_samples_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_churn_observed_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS zonaprop_segment_scan_history (
    id                  BIGSERIAL PRIMARY KEY,
    segment_id          BIGINT NOT NULL REFERENCES zonaprop_segments(id),
    scanned_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Contexto del segmento al momento del scan
    operation_key       TEXT,
    province_key        TEXT,
    price_min           NUMERIC,
    price_max           NUMERIC,
    surface_min         NUMERIC,
    surface_max         NUMERIC,
    total_count         INTEGER,
    delta_total_count   INTEGER,

    -- Decisión que llevó al scan
    tier                TEXT,
    priority            TEXT,
    age_hours           NUMERIC,
    estimated_pages     INTEGER,
    batch_id            TEXT,

    -- Resultado del scan
    new_count           INTEGER,
    changed_count       INTEGER,
    listings_found      INTEGER,
    churn_raw           NUMERIC,
    churn_daily         NUMERIC,
    churn_ewma          NUMERIC,
    churn_samples_count INTEGER,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_zonaprop_scan_history_seg_at
    ON zonaprop_segment_scan_history (segment_id, scanned_at);

CREATE INDEX IF NOT EXISTS idx_zonaprop_scan_history_batch
    ON zonaprop_segment_scan_history (batch_id)
    WHERE batch_id IS NOT NULL;
