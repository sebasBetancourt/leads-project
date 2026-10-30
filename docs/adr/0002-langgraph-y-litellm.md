# ADR 0002: LangGraph para el agente y LiteLLM como capa de modelos

**Estado:** aceptada (2026-10-30)

## Contexto
El agente sigue pasos conocidos (investigar → calificar → redactar) con una bifurcación (califica o no).
Queremos poder cambiar de proveedor de IA (Claude, OpenAI, Ollama) sin tocar código.

## Decisión
- **LangGraph** con un grafo por lead (patrón de kaymen99): estado tipado, nodos con responsabilidad única,
  investigación en paralelo. El bucle "siguiente lead" vive en el worker, no en el grafo, para que cada lead
  se pueda reintentar solo.
- **LiteLLM** detrás de `core/llm.py`: todo el código llama a `complete` o `complete_structured`; el modelo
  se elige con `LLM_MODEL` en el `.env`.
- Salidas validadas con Pydantic (`agents/schemas.py`).

## Consecuencias
- Cambiar de modelo es una línea en `.env`. Los modelos locales pueden no soportar salida estructurada igual de bien.
- Descartado CrewAI: su modelo de roles agrega complejidad que este flujo lineal no necesita.
