"""Única puerta de entrada a los modelos de IA (ver docs/adr/0002).

Todo el código llama a `complete` o `complete_structured`; el proveedor se elige con LLM_MODEL
en el .env (Groq, Claude, OpenAI, Ollama...) gracias a LiteLLM. Cada tarea del agente puede usar
un modelo distinto (LLM_MODEL_QUALIFY, LLM_MODEL_DRAFT); si no se define, usa LLM_MODEL.
"""

import json
import logging
import re
from dataclasses import dataclass, field

import litellm
from pydantic import BaseModel, ValidationError

from leadbot.core.config import get_settings

log = logging.getLogger(__name__)

# El modelo a veces envuelve el JSON en ```json ... ``` aunque se le pida solo JSON.
FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class LLMError(RuntimeError):
    """El modelo no respondió o no devolvió una salida válida tras los reintentos."""


@dataclass
class LLMUsage:
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, response) -> None:
        usage = getattr(response, "usage", None)
        self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        self.calls += 1
        try:
            self.cost_usd += litellm.completion_cost(completion_response=response) or 0.0
        except Exception:  # modelo sin precio en LiteLLM: el costo queda en 0, no es un error
            pass


@dataclass
class Structured[T: BaseModel]:
    value: T
    usage: LLMUsage = field(repr=False)


def _call(model: str, messages: list[dict], *, temperature: float, response_format=None):
    settings = get_settings()
    kwargs = {"response_format": response_format} if response_format is not None else {}
    try:
        return litellm.completion(
            model=model,
            messages=messages,
            temperature=temperature,
            timeout=settings.llm_timeout_seconds,
            num_retries=settings.llm_num_retries,  # errores de red y 429: LiteLLM reintenta con espera
            **kwargs,
        )
    except Exception as e:
        raise LLMError(f"{model}: {type(e).__name__}: {e}"[:500]) from e


def extract_json(text: str) -> str:
    """Quita cercas de markdown y texto alrededor del objeto JSON."""
    text = (text or "").strip()
    if match := FENCE_RE.match(text):
        text = match.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def complete(system: str, user: str, *, task: str = "", temperature: float = 0.4) -> str:
    model = get_settings().model_for(task)
    response = _call(
        model, [{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=temperature
    )
    return response.choices[0].message.content


def complete_structured[T: BaseModel](
    system: str, user: str, schema: type[T], *, task: str = "", temperature: float = 0.2
) -> Structured[T]:
    """Respuesta validada contra un modelo Pydantic (salida estructurada, patrón kaymen99).

    Si la respuesta no valida, se le devuelve el error al modelo para que la corrija
    (hasta LLM_MAX_VALIDATION_ATTEMPTS intentos). LLM_JSON_MODE elige cómo se pide el JSON:
    - schema: response_format con el JSON Schema (modelos con salida estructurada estricta).
    - json_object: modo JSON simple, con el esquema dentro del prompt (más modelos lo soportan).
    """
    settings = get_settings()
    model = settings.model_for(task)
    if settings.llm_json_mode == "schema":
        response_format = schema
    else:
        response_format = {"type": "json_object"}
        system += (
            "\n\nResponde únicamente con un objeto JSON que cumpla este JSON Schema, sin texto adicional:\n"
            + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        )

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    usage = LLMUsage(model=model)
    last_error = ""
    for attempt in range(1, settings.llm_max_validation_attempts + 1):
        response = _call(model, messages, temperature=temperature, response_format=response_format)
        usage.add(response)
        content = response.choices[0].message.content or ""
        try:
            return Structured(schema.model_validate_json(extract_json(content)), usage)
        except ValidationError as e:
            last_error = str(e)[:1500]
            log.warning("%s: salida inválida (intento %d): %s", model, attempt, last_error[:200])
            messages += [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": f"Tu respuesta no cumple el esquema:\n{last_error}\n"
                    "Devuelve solo el JSON corregido, completo.",
                },
            ]
    raise LLMError(f"{model}: salida inválida tras {settings.llm_max_validation_attempts} intentos: {last_error}")
