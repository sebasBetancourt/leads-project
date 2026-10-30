# ADR 0004: Google Sheets como interfaz del MVP

**Estado:** aceptada (2026-10-30)

## Contexto
Se necesita revisar leads, editar y aprobar mensajes y marcar respuestas de redes, sin construir un panel web todavía.

## Decisión
Google Sheets (gspread + cuenta de servicio) con cuatro pestañas: Leads, Aprobación, Respuestas y Métricas.
Postgres sigue siendo la fuente de verdad: Sheets solo puede cambiar columnas editables definidas en `sheets/sync.py`.

## Consecuencias
- Cero frontend en el MVP; se puede usar desde el celular.
- Límites de cuota de la API de Sheets: sincronizar por lotes cada pocos minutos, no por fila.
- Cuando se construya un panel web (FastAPI), reemplaza a `sheets/` sin tocar el resto.
