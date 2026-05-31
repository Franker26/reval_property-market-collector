-- 002_add_listing_enriched_fields.sql
-- Agrega columnas de enriquecimiento a listing_entities y listing_snapshots.
-- También migra antiguedad y orientacion de extra_data a sus columnas propias.

BEGIN;

-- ── listing_entities ──────────────────────────────────────────────────────────
ALTER TABLE listing_entities ADD COLUMN IF NOT EXISTS generated_title TEXT;
ALTER TABLE listing_entities ADD COLUMN IF NOT EXISTS description      TEXT;
ALTER TABLE listing_entities ADD COLUMN IF NOT EXISTS toilettes        INTEGER;
ALTER TABLE listing_entities ADD COLUMN IF NOT EXISTS antiquity_years  INTEGER;
ALTER TABLE listing_entities ADD COLUMN IF NOT EXISTS disposition      TEXT;
ALTER TABLE listing_entities ADD COLUMN IF NOT EXISTS orientation      TEXT;

-- ── listing_snapshots (mismos campos, append-only) ────────────────────────────
ALTER TABLE listing_snapshots ADD COLUMN IF NOT EXISTS generated_title TEXT;
ALTER TABLE listing_snapshots ADD COLUMN IF NOT EXISTS description      TEXT;
ALTER TABLE listing_snapshots ADD COLUMN IF NOT EXISTS toilettes        INTEGER;
ALTER TABLE listing_snapshots ADD COLUMN IF NOT EXISTS antiquity_years  INTEGER;
ALTER TABLE listing_snapshots ADD COLUMN IF NOT EXISTS disposition      TEXT;
ALTER TABLE listing_snapshots ADD COLUMN IF NOT EXISTS orientation      TEXT;

-- ── Migrar datos existentes desde extra_data ──────────────────────────────────
-- antiguedad → antiquity_years
-- orientacion → orientation
-- Se elimina la clave de extra_data para no duplicar.
UPDATE listing_entities
SET
    antiquity_years = NULLIF(extra_data->>'antiguedad', '')::INTEGER,
    orientation     = NULLIF(extra_data->>'orientacion', ''),
    extra_data      = extra_data - 'antiguedad' - 'orientacion'
WHERE extra_data IS NOT NULL
  AND (extra_data ? 'antiguedad' OR extra_data ? 'orientacion');

COMMIT;
