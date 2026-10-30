from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from leadbot.core import llm
from leadbot.core.config import Settings


class Answer(BaseModel):
    score: int
    reason: str


def fake_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )


@pytest.fixture
def calls(monkeypatch):
    """Llamadas al proveedor; cada prueba pone en `replies` lo que responde el modelo."""
    recorded = {"replies": [], "calls": []}

    def completion(**kwargs):
        recorded["calls"].append(kwargs)
        return fake_response(recorded["replies"].pop(0))

    monkeypatch.setattr(llm.litellm, "completion", completion)
    monkeypatch.setattr(llm.litellm, "completion_cost", lambda **_: 0.001)
    monkeypatch.setattr(llm, "get_settings", lambda: Settings(llm_model="groq/test-model", _env_file=None))
    return recorded


def test_extract_json_strips_markdown_fences_and_chatter():
    assert llm.extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert llm.extract_json('Claro, aquí está: {"a": 1} ¡listo!') == '{"a": 1}'


def test_structured_output_is_validated_and_counts_usage(calls):
    calls["replies"] = ['{"score": 80, "reason": "vende por WhatsApp"}']
    result = llm.complete_structured("sys", "user", Answer, task="qualify")
    assert result.value == Answer(score=80, reason="vende por WhatsApp")
    assert result.usage.model == "groq/test-model"
    assert result.usage.tokens == 120
    assert result.usage.cost_usd == pytest.approx(0.001)
    assert calls["calls"][0]["response_format"] is Answer


def test_invalid_output_is_sent_back_for_correction(calls):
    calls["replies"] = ['{"score": "alto"}', '{"score": 70, "reason": "ok"}']
    result = llm.complete_structured("sys", "user", Answer)
    assert result.value.score == 70
    assert result.usage.calls == 2
    retry_messages = calls["calls"][1]["messages"]
    assert retry_messages[-2] == {"role": "assistant", "content": '{"score": "alto"}'}
    assert "no cumple el esquema" in retry_messages[-1]["content"]


def test_gives_up_after_max_attempts(calls):
    calls["replies"] = ["no es json", "tampoco"]
    with pytest.raises(llm.LLMError, match="salida inválida"):
        llm.complete_structured("sys", "user", Answer)


def test_json_object_mode_puts_the_schema_in_the_prompt(calls, monkeypatch):
    monkeypatch.setattr(llm, "get_settings", lambda: Settings(llm_json_mode="json_object", _env_file=None))
    calls["replies"] = ['{"score": 10, "reason": "cadena"}']
    llm.complete_structured("sys", "user", Answer)
    call = calls["calls"][0]
    assert call["response_format"] == {"type": "json_object"}
    assert '"score"' in call["messages"][0]["content"]


def test_provider_errors_become_llm_errors(monkeypatch):
    def boom(**_):
        raise TimeoutError("sin respuesta")

    monkeypatch.setattr(llm.litellm, "completion", boom)
    with pytest.raises(llm.LLMError, match="TimeoutError"):
        llm.complete("sys", "user")


def test_model_for_task_falls_back_to_the_general_model():
    settings = Settings(llm_model="groq/general", llm_model_draft="groq/writer", _env_file=None)
    assert settings.model_for("draft") == "groq/writer"
    assert settings.model_for("qualify") == "groq/general"
    assert settings.model_for("") == "groq/general"
