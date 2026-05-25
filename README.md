# reval — property-market-collector

Servicio de recopilación de datos históricos de publicaciones inmobiliarias para market intelligence.

Dado un conjunto de URLs de portales inmobiliarios, extrae los datos estructurados de cada publicación y los almacena para construir un historial de precios y condiciones de mercado.

## Propósito

Este proyecto es la base de un **virtual cellar de propiedades**: al trackear URLs a lo largo del tiempo se puede observar la evolución del precio, el tiempo en mercado y otras variables de cada publicación. El objetivo es generar inteligencia de mercado con datos históricos propios.

## Estructura

```
property-market-collector/    ← microservicio de extracción (FastAPI)
```

→ Ver documentación técnica en [property-market-collector/README.md](property-market-collector/README.md)
