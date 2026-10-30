# ADR 0003: Postgres como base de datos y como cola de trabajos

**Estado:** aceptada (2026-10-30)

## Contexto
Hay trabajo en segundo plano (discovery, enrichment, agente, envíos) con reintentos y pausas entre envíos.

## Decisión
Una sola base Postgres 16. La cola es la tabla `jobs`, consumida con `SELECT ... FOR UPDATE SKIP LOCKED`
(patrón de AgentSDR). Sin Redis ni broker. Un único worker con APScheduler.

## Consecuencias
- Una sola pieza de infraestructura para respaldar y operar.
- Suficiente para cientos de envíos al día; si algún día se necesitan miles por minuto, se revisa.
- El scheduler no tiene lock distribuido: debe correr **una sola instancia** del worker.
