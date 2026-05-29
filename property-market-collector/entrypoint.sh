#!/bin/bash
set -e

echo "Inicializando esquema de base de datos..."
python -c "
from app.db.session import get_sync_engine
from app.db.models import Base
engine = get_sync_engine()
Base.metadata.create_all(engine)
print('Schema listo.')
"

echo "Iniciando servidor..."
exec uvicorn main:app --host 0.0.0.0 --port 8200
