-- 001_rename_zonaprop_tables.sql
-- Renombra las tablas genéricas a nombres específicos de Zonaprop.
-- Ejecutar una vez, con la DB corriendo y sin tráfico activo.
-- Prerequisito: segment_discovery debe haber finalizado.

BEGIN;

-- ── 1. market_segments → zonaprop_segments ────────────────────────────────────
ALTER TABLE market_segments RENAME TO zonaprop_segments;

ALTER INDEX idx_market_segments_portal_op_prov
    RENAME TO idx_zonaprop_segments_portal_op_prov;

ALTER INDEX idx_market_segments_leaf
    RENAME TO idx_zonaprop_segments_leaf;

ALTER TABLE zonaprop_segments
    RENAME CONSTRAINT uq_market_segments_boundaries
    TO uq_zonaprop_segments_boundaries;


-- ── 2. segment_snapshots → zonaprop_segment_snapshots ────────────────────────
ALTER TABLE segment_snapshots RENAME TO zonaprop_segment_snapshots;

ALTER INDEX idx_segment_snapshots_segment_captured
    RENAME TO idx_zonaprop_segment_snapshots_captured;


-- ── 3. url_discovery_segment_runs → zonaprop_segment_scan_queue ──────────────
ALTER TABLE url_discovery_segment_runs RENAME TO zonaprop_segment_scan_queue;

ALTER INDEX idx_url_discovery_runs_status_seg
    RENAME TO idx_zonaprop_scan_queue_status_seg;


-- ── 4. listing_entities — eliminar segment_id ────────────────────────────────
-- El índice idx_listing_entities_segment_status se elimina automáticamente
-- porque depende de la columna segment_id.
ALTER TABLE listing_entities DROP COLUMN IF EXISTS segment_id;


COMMIT;
