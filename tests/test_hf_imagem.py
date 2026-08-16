"""Testes offline da geração de imagem pela Hugging Face."""
import os
import sys
import types

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai  # noqa: E402
import config  # noqa: E402
from ai import AIError  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"imagem-de-teste" * 50


class FakeResponse:
    def __init__(self, status_code, data=None, headers=None):
        self.status_code = status_code
        self._data = data or {}
        self.headers = headers or {}

    def json(self):
        return self._data


class FakeHTTPError(Exception):
    def __init__(self, status, message="erro", data=None, headers=None):
        super().__init__(message)
        self.response = FakeResponse(status, data=data, headers=headers)


class FakeConnectError(Exception):
    pass


class FakeClient:
    def __init__(self):
        self.calls = []
        self.responder = lambda prompt, model: PNG

    def text_to_image(self, prompt, model=None):
        self.calls.append((model, prompt))
        return self.responder(prompt, model)


@pytest.fixture
def hf(monkeypatch):
    fake = FakeClient()
    fake.timeouts = []

    def factory(timeout=ai.HF_TIMEOUT):
        fake.timeouts.append(timeout)
        return fake

    monkeypatch.setattr(ai, "_create_hf_client", factory)
    monkeypatch.setattr(config, "HF_TOKEN", "hf_token_de_teste")
    monkeypatch.setattr(ai.time, "sleep", lambda seconds: None)
    return fake


def test_cadeia_exata_na_ordem_pedida():
    assert config.HF_IMAGE_MODELS == (
        "warp-ai/wuerstchen",
        "black-forest-labs/FLUX.1-schnell",
        "stabilityai/stable-diffusion-xl-base-1.0",
        "stable-diffusion-v1-5/stable-diffusion-v1-5",
        "prompthero/openjourney",
    )


def test_cliente_oficial_recebe_token_provider_e_timeout(monkeypatch):
    captured = {}

    class InferenceClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    module = types.ModuleType("huggingface_hub")
    module.InferenceClient = InferenceClient
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)
    monkeypatch.setattr(config, "HF_TOKEN", "hf_segredo")

    assert isinstance(ai._create_hf_client(45), InferenceClient)
    assert captured == {
        "provider": "auto",
        "api_key": "hf_segredo",
        "timeout": 45,
    }


def test_sucesso_para_no_primeiro_modelo(hf):
    assert ai.generate_image("um gato astronauta") == PNG
    assert hf.calls == [(config.HF_IMAGE_MODELS[0], "um gato astronauta")]
    assert hf.timeouts and 0 < hf.timeouts[0] <= ai.HF_TIMEOUT


def test_pil_e_convertida_para_png(hf):
    hf.responder = lambda prompt, model: Image.new("RGBA", (64, 32), "blue")
    data = ai.generate_image("quadrado azul")
    assert data.startswith(b"\x89PNG\r\n\x1a\n")


def test_wuerstchen_indisponivel_cai_para_flux(hf):
    def responder(prompt, model):
        if model == config.HF_IMAGE_MODELS[0]:
            raise FakeHTTPError(404, "model has no inference provider")
        return PNG

    hf.responder = responder
    assert ai.generate_image("um castelo") == PNG
    assert [model for model, _ in hf.calls] == list(config.HF_IMAGE_MODELS[:2])


def test_resposta_invalida_cai_para_proximo_modelo(hf):
    hf.responder = lambda prompt, model: (
        b"nao e imagem" if model == config.HF_IMAGE_MODELS[0] else PNG
    )
    assert ai.generate_image("teste") == PNG
    assert [model for model, _ in hf.calls] == list(config.HF_IMAGE_MODELS[:2])


def test_503_retenta_e_depois_cai_para_sdxl(hf, monkeypatch):
    sleeps = []
    monkeypatch.setattr(ai.time, "sleep", sleeps.append)

    def responder(prompt, model):
        if model == config.HF_IMAGE_MODELS[0]:
            raise FakeHTTPError(404, "sem provider")
        if model == config.HF_IMAGE_MODELS[1]:
            raise FakeHTTPError(
                503,
                "loading",
                data={"estimated_time": 12},
                headers={"Retry-After": "4"},
            )
        return PNG

    hf.responder = responder
    assert ai.generate_image("teste") == PNG
    models = [model for model, _ in hf.calls]
    assert models == [
        config.HF_IMAGE_MODELS[0],
        config.HF_IMAGE_MODELS[1],
        config.HF_IMAGE_MODELS[1],
        config.HF_IMAGE_MODELS[2],
    ]
    assert sleeps == [12]


@pytest.mark.parametrize("status", [401, 403])
def test_token_invalida_aborta_a_cadeia(hf, status):
    hf.responder = lambda prompt, model: (_ for _ in ()).throw(
        FakeHTTPError(status, "token permission denied")
    )
    with pytest.raises(AIError, match="token"):
        ai.generate_image("teste")
    assert len(hf.calls) == 1


def test_403_de_modelo_faz_fallback(hf):
    def responder(prompt, model):
        if model == config.HF_IMAGE_MODELS[0]:
            raise FakeHTTPError(403, "model is gated")
        return PNG

    hf.responder = responder
    assert ai.generate_image("teste") == PNG
    assert len(hf.calls) == 2


def test_creditos_esgotados_abortam_a_cadeia(hf):
    hf.responder = lambda prompt, model: (_ for _ in ()).throw(
        FakeHTTPError(402, "billing credits exhausted")
    )
    with pytest.raises(AIError, match="créditos"):
        ai.generate_image("teste")
    assert len(hf.calls) == 1


def test_sem_token_nao_cria_cliente(monkeypatch):
    monkeypatch.setattr(config, "HF_TOKEN", "")
    monkeypatch.setattr(
        ai,
        "_create_hf_client",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não devia chamar")),
    )
    with pytest.raises(AIError, match="HF_TOKEN"):
        ai.generate_image("teste")


@pytest.mark.parametrize("prompt", ["", "   "])
def test_prompt_vazio_falha_sem_cliente(monkeypatch, prompt):
    monkeypatch.setattr(
        ai,
        "_create_hf_client",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não devia chamar")),
    )
    with pytest.raises(AIError, match="Descreva"):
        ai.generate_image(prompt)


def test_prompt_longo_falha_sem_cliente(monkeypatch):
    monkeypatch.setattr(config, "HF_TOKEN", "hf_teste")
    monkeypatch.setattr(
        ai,
        "_create_hf_client",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não devia chamar")),
    )
    with pytest.raises(AIError, match="longa demais"):
        ai.generate_image("x" * (ai.HF_MAX_PROMPT_CHARS + 1))


def test_imagem_grande_demais_e_rejeitada():
    class HugeImage:
        size = (5000, 5000)

        def save(self, *args, **kwargs):
            raise AssertionError("não deveria serializar")

    with pytest.raises(ai._ModeloIndisponivel, match="megapixels"):
        ai._hf_image_bytes(HugeImage())


def test_bytes_grandes_demais_sao_rejeitados():
    huge = b"\x89PNG\r\n\x1a\n" + b"x" * ai.HF_MAX_IMAGE_BYTES
    with pytest.raises(ai._ModeloIndisponivel, match="10 MB"):
        ai._hf_image_bytes(huge)


def test_connect_error_retenta(hf, monkeypatch):
    attempts = {"count": 0}
    sleeps = []
    monkeypatch.setattr(ai.time, "sleep", sleeps.append)

    def responder(prompt, model):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise FakeConnectError("connection reset")
        return PNG

    hf.responder = responder
    assert ai.generate_image("teste") == PNG
    assert len(hf.calls) == 2
    assert sleeps == [1]


def test_sleep_e_cortado_pelo_deadline(hf, monkeypatch):
    attempts = {"count": 0}
    sleeps = []
    clock = iter([100.0, 100.75, 100.8])
    monkeypatch.setattr(ai.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(ai.time, "sleep", sleeps.append)

    def responder(prompt, model):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise FakeHTTPError(503, "loading", data={"estimated_time": 30})
        return PNG

    hf.responder = responder
    assert ai._hf_image_model(config.HF_IMAGE_MODELS[0], "teste", 101.0) == PNG
    assert sleeps == [0.25]


def test_deadline_global_aborta_sem_chamar_cliente(monkeypatch):
    clock = iter([100.0, 281.0])
    monkeypatch.setattr(ai.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(config, "HF_TOKEN", "hf_teste")
    monkeypatch.setattr(
        ai,
        "_create_hf_client",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não devia chamar")),
    )
    with pytest.raises(AIError, match="180 segundos"):
        ai.generate_image("teste")


def test_todos_indisponiveis_tentam_a_cadeia_e_sanitizam(hf):
    hf.responder = lambda prompt, model: (_ for _ in ()).throw(
        FakeHTTPError(
            410,
            "provider unavailable hf_token_de_teste\nsegundo detalhe",
        )
    )
    with pytest.raises(AIError) as exc:
        ai.generate_image("teste")
    assert [model for model, _ in hf.calls] == list(config.HF_IMAGE_MODELS)
    message = str(exc.value)
    assert "Nenhum modelo" in message
    assert "\n" not in message
    assert "hf_token_de_teste" not in message


def test_nao_usa_requests_nem_openrouter(hf, monkeypatch):
    monkeypatch.setattr(
        ai.requests,
        "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("HTTP manual proibido")),
    )
    assert ai.generate_image("teste") == PNG
