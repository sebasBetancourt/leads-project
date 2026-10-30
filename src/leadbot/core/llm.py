"""Única puerta de entrada a los modelos de IA (ver docs/adr/0002).

Todo el código llama a `complete` o `complete_structured`; el proveedor se elige con LLM_MODEL
en el .env (Claude, OpenAI, Ollama...) gracias a LiteLLM.
"""

import litellm
from pydantic import BaseModel

from leadbot.core.config import get_settings


def complete(system: str, user: str, *, temperature: float = 0.4) -> str:
    response = litellm.completion(
        model=get_settings().llm_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
    )
    return response.choices[0].message.content


def complete_structured[T: BaseModel](system: str, user: str, schema: type[T], *, temperature: float = 0.2) -> T:
    """Respuesta validada contra un modelo Pydantic (salida estructurada, patrón kaymen99)."""
    response = litellm.completion(
        model=get_settings().llm_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        response_format=schema,
    )
    return schema.model_validate_json(response.choices[0].message.content)
