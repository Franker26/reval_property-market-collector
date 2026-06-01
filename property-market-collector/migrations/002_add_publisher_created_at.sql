-- Agrega publisher_created_at a listing_entities y listing_snapshots.
-- Este campo almacena la fecha en que el portal declara haber publicado el listing.
-- Se diferencia de first_seen_at (primera vez que Reval observó la publicación).
--
-- Aplicar manualmente antes de correr jobs/backfill_publisher_created_at.py

ALTER TABLE listing_entities
    ADD COLUMN IF NOT EXISTS publisher_created_at TIMESTAMPTZ;

ALTER TABLE listing_snapshots
    ADD COLUMN IF NOT EXISTS publisher_created_at TIMESTAMPTZ;
