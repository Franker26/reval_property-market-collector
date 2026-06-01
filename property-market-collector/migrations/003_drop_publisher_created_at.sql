-- Elimina publisher_created_at de ambas tablas.
-- El campo fue identificado erróneamente: raw["publisher"]["created_date"] es la
-- fecha de registro del publicador en la plataforma, no la fecha del aviso.
-- El campo publisher_created_date vuelve a extra_data como string crudo.

ALTER TABLE listing_entities DROP COLUMN IF EXISTS publisher_created_at;
ALTER TABLE listing_snapshots DROP COLUMN IF EXISTS publisher_created_at;
