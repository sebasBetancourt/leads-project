# ADR 0001: Usar gosom/google-maps-scraper como servicio, no escribir un scraper propio

**Estado:** aceptada (2026-10-30)

## Contexto
Necesitamos negocios de Google Maps (nombre, teléfono, web, rating, reseñas) en 20 ciudades y varios sectores.
Mantener un scraper de Google Maps propio es costoso: Google cambia la página con frecuencia.

## Decisión
Correr `gosom/google-maps-scraper` (MIT, ~7.000 estrellas, activo) como contenedor en modo web y usar su
REST API (`POST /api/v1/jobs`, `GET /api/v1/jobs/{id}/download`). La imagen se fija a una versión (`v1.18.1`).

## Consecuencias
- No mantenemos código de scraping de Maps; actualizar es cambiar la etiqueta de la imagen.
- Dependemos del formato de su CSV: `discovery/gmaps_client.py` es la única pieza que lo conoce.
- Sin proxies, volúmenes grandes pueden ser bloqueados por Google. Empezamos con prioridad 1 de ciudades.
