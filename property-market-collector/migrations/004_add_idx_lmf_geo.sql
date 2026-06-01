-- Índice compuesto en listing_market_facts para filtrado geográfico por bounding box.
-- Usado como pre-filtro antes de aplicar Haversine en /market/facts/search.
CREATE INDEX IF NOT EXISTS idx_lmf_geo
    ON listing_market_facts (latitude, longitude)
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL;
