# property-market-collector

Microservicio de extracción de publicaciones inmobiliarias a partir de URLs. Dado un enlace de un portal soportado, devuelve los datos estructurados de la publicación.

Es el componente de captura del **property-market-collector**: un sistema externo le pasa URLs (obtenidas por cualquier medio) y este servicio extrae el snapshot de datos en ese momento. Repetido en el tiempo, eso construye el historial de cada propiedad.

## Portales soportados

| Portal              | Dominio                          | Tecnología         |
|---------------------|----------------------------------|--------------------|
| ZonaProp            | zonaprop.com.ar                  | httpx + JSON state |
| Argenprop           | argenprop.com                    | httpx + JSON-LD    |
| MercadoLibre        | inmuebles.mercadolibre.com.ar    | Playwright (SPA)   |
| La Capital          | inmuebles.lacapital.com.ar       | httpx + HTML       |
| Propia              | propia.com.ar                    | httpx + JSON-LD    |
| LiderProp           | liderprop.com                    | httpx + JSON-LD    |
| Inmoclick           | inmoclick.com                    | httpx + JSON-LD    |
| BuscadorProp        | buscadorprop.com.ar              | httpx + JSON-LD    |
| BuscaInmueble       | buscainmueble.com                | httpx + JSON-LD    |
| Clarín Inmuebles    | inmuebles.clarin.com             | httpx + JSON-LD    |
| Doomos AR           | ar.doomos.com                    | Playwright (SPA)   |

---

## Correr el servicio

```bash
docker compose up --build
```

El servicio queda disponible en `http://localhost:8100`.

### Variables de entorno

| Variable        | Default   | Descripción                                                                               |
|-----------------|-----------|-------------------------------------------------------------------------------------------|
| `SERVICE_TOKEN` | *(vacío)* | Token Bearer requerido en todas las requests. Si está vacío, no se valida autenticación. |

```bash
SERVICE_TOKEN=mi-token docker compose up
```

---

## API

### `GET /health`

Verifica que el servicio esté activo. No requiere autenticación.

```bash
curl http://localhost:8100/health
```

```json
{
  "status": "ok",
  "sources": ["zonaprop", "argenprop", "mercadolibre", ...]
}
```

---

### `POST /extract`

Extrae los datos de una publicación individual a partir de su URL.

```bash
curl -X POST http://localhost:8100/extract \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.zonaprop.com.ar/propiedades/departamento-en-alquiler-palermo-47979890.html"}'
```

**Request**

| Campo | Tipo   | Descripción                                      |
|-------|--------|--------------------------------------------------|
| `url` | string | URL de la publicación en un portal soportado     |

**Response 200**

```json
{
  "url": "https://www.zonaprop.com.ar/propiedades/...",
  "portal": "zonaprop",
  "imagen_url": "https://cdn.zonaprop.com.ar/img/...",
  "precio": 95000,
  "direccion": "Av. Santa Fe 1234, Palermo",
  "tipo": "Departamento",
  "ambientes": 3,
  "dias_mercado": 12,
  "superficie_total": 70.0,
  "superficie_cubierta": 65.0,
  "superficie_semicubierta": null,
  "superficie_descubierta": null,
  "antiguedad": 15,
  "orientacion": "Norte",
  "piso": 4,
  "cochera": true,
  "pileta": null
}
```

Todos los campos son opcionales — si el portal no publica el dato, el campo es `null`.

**Errores**

| Status | Causa                                       |
|--------|---------------------------------------------|
| 400    | URL de un portal no soportado               |
| 401    | Token inválido o ausente                    |
| 422    | Página accesible pero sin datos parseables  |
| 502    | Error al acceder al portal                  |

---

## Colección Bruno

Incluye colección [Bruno](https://www.usebruno.com/) lista para usar en `bruno/`.

```
bruno/
├── environments/local.bru   ← base_url y service_token
└── scraper-service/
    ├── health.bru
    └── extract/             ← un ejemplo por portal + caso de error
```

Para empezar: abrí Bruno → Open Collection → seleccioná la carpeta `bruno/`.
Configurá el environment `local` con tu `service_token` si corresponde.

---

## Arquitectura

```
POST /extract
  └─ sources.extract(url, client)
        └─ encuentra el BaseSource que soporta la URL
              └─ source.extract(url, client) → PropertyListing
```

- **Detección de portal por URL**: `BaseSource.can_handle(url)` identifica automáticamente qué extractor usar según el dominio.
- **Portales SPA** (MercadoLibre, Doomos) usan Playwright (Chromium headless).
- **Portales server-rendered** usan httpx con fallback automático a Playwright si reciben 403.

```
sources/
├── _common.py        ← fetch_html, parse_ldjson, slugify
├── base.py           ← clase abstracta BaseSource
├── browser.py        ← singleton Playwright compartido
├── models.py         ← PropertyListing
├── zonaprop.py
├── argenprop.py
├── mercadolibre.py
├── lacapital.py
├── propia.py
├── liderprop.py
├── inmoclick.py
├── buscadorprop.py
├── buscainmueble.py
├── clarin.py
└── doomos.py
```
