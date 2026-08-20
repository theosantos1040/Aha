"""Testa as mensagens de erro da IA quando o limite gratuito da OpenRouter
é atingido — sem tocar a rede.

Sintoma que isso resolve: "a IA responde uma vez e depois diz que está
indisponível". A causa mais provável é o limite de free-tier da OpenRouter
(20 pedidos/min, ou 50/dia sem créditos comprados — COMPARTILHADO entre
TODOS os modelos ":free" da conta). Antes, o bot escondia isso atrás de um
genérico "Todos os modelos de IA falharam no momento. Tente novamente.",
que lê como bug em vez de "aguarde/adicione créditos".
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai  # noqa: E402
import config  # noqa: E402
from ai import AIError  # noqa: E402


class FakeResponse:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data or {}
        self.text = str(data)[:200]

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def sem_rede_de_verdade(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-or-teste-fake")
    monkeypatch.setattr(ai.time, "sleep", lambda s: None)


def _sempre_429(mensagem="Rate limit exceeded: free-tier limit reached"):
    def post(url, headers=None, json=None, timeout=None):
        return FakeResponse(429, {
            "error": {"message": mensagem, "metadata": {"retry_after_seconds": 0}}
        })
    return post


def _sempre_500(mensagem="Internal server error, tente mais tarde"):
    def post(url, headers=None, json=None, timeout=None):
        return FakeResponse(500, {"error": {"message": mensagem}})
    return post


def test_chat_com_429_em_todos_os_modelos_explica_o_limite_gratuito(monkeypatch):
    monkeypatch.setattr(ai.requests, "post", _sempre_429())
    with pytest.raises(AIError) as exc:
        ai.chat("oi")
    msg = str(exc.value)
    assert "limite gratuito" in msg.lower()
    assert "openrouter.ai/credits" in msg
    print("✓ 429 em todos os modelos explica o limite gratuito (não um genérico confuso)")


def test_chat_com_500_generico_nao_vira_mensagem_de_rate_limit(monkeypatch):
    """REGRESSÃO INVERSA: erro genérico não pode ser rotulado como rate-limit
    — senão o usuário espera de graça um problema que não vai se resolver."""
    monkeypatch.setattr(ai.requests, "post", _sempre_500())
    with pytest.raises(AIError) as exc:
        ai.chat("oi")
    msg = str(exc.value)
    assert "limite gratuito" not in msg.lower()
    assert "Todos os modelos de IA falharam" in msg
    print("✓ erro genérico (não rate-limit) mantém a mensagem genérica")


def test_request_preserva_a_mensagem_real_da_openrouter(monkeypatch):
    """A mensagem de erro real da OpenRouter (não só o código HTTP) precisa
    sobreviver até o usuário — antes 'last_err' só guardava 'HTTP 429'."""
    monkeypatch.setattr(ai.requests, "post", _sempre_429("mensagem bem específica X9"))
    with pytest.raises(AIError) as exc:
        ai._call_model("modelo-fake", [{"role": "user", "content": "oi"}], timeout=5)
    assert "mensagem bem específica X9" in str(exc.value)
    print("✓ a mensagem real da OpenRouter chega até o AIError final")


def test_vision_com_429_em_todos_os_fallbacks_explica_o_limite(monkeypatch):
    monkeypatch.setattr(ai.requests, "post", _sempre_429())
    with pytest.raises(AIError) as exc:
        ai.vision("o que é isso?", b"\x89PNG\r\n\x1a\n" + b"x" * 20)
    assert "limite gratuito" in str(exc.value).lower()
    print("✓ /analiseia com 429 em todos os fallbacks também explica o limite")


def test_transcribe_com_429_em_todos_os_fallbacks_explica_o_limite(monkeypatch):
    monkeypatch.setattr(ai.requests, "post", _sempre_429())
    # áudio já em mp3 (magic bytes ID3) pra não precisar de ffmpeg no teste
    audio_mp3 = b"ID3" + b"\x00" * 20
    with pytest.raises(AIError) as exc:
        ai.transcribe(audio_mp3)
    assert "limite gratuito" in str(exc.value).lower()
    print("✓ /transcrever com 429 em todos os fallbacks também explica o limite")


def test_search_com_429_preserva_mensagem_real_mesmo_sem_fallback(monkeypatch):
    """search() não tem cadeia de fallback (1 modelo só), mas ainda assim
    precisa mostrar a mensagem real da OpenRouter, não só 'HTTP 429'."""
    monkeypatch.setattr(ai.requests, "post", _sempre_429("cota diária esgotada"))
    with pytest.raises(AIError) as exc:
        ai.search("python")
    assert "cota diária esgotada" in str(exc.value)
    print("✓ /pesquisa preserva a mensagem real mesmo sem cadeia de fallback")


if __name__ == "__main__":
    print("Rode com: python -m pytest tests/test_ai_mensagens.py -v")
