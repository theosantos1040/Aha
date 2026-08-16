"""Testes do /gerarimagem via Hugging Face — SEM tocar a rede.

Tudo é mockado com monkeypatch em requests.post/requests.get e em time.sleep,
então os testes rodam offline e em milissegundos. O que cada teste garante:

- sucesso no primeiro modelo da cadeia (formato b64_json);
- formato alternativo `url` (o provedor manda link e o bot baixa os bytes);
- 404 na rota nova cai para a rota antiga /hf-inference (bytes crus);
- 404/410 derrubam o modelo e a cadeia desce até o FLUX.1-schnell;
- 401 vira mensagem em português falando da token;
- sem HF_TOKEN nem chega a abrir conexão;
- 503 respeita o estimated_time (com teto de 30s) sem dormir de verdade;
- todos falhando -> AIError listando os erros reais;
- toda requisição leva timeout e o header Authorization certo.
"""
import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai  # noqa: E402
import config  # noqa: E402
from ai import AIError  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"imagem-de-mentira" * 50
JPEG = b"\xff\xd8\xff\xe0" + b"outra-imagem" * 50
PNG_B64 = base64.b64encode(PNG).decode()

ROTA_NOVA = "https://router.huggingface.co/v1/images/generations"
ROTA_ANTIGA = "https://router.huggingface.co/hf-inference/models/"


class FakeResp:
    """Resposta falsa de requests, no mínimo que o ai.py usa."""

    def __init__(self, status_code=200, data=None, text="", content=b"", headers=None):
        self.status_code = status_code
        self._data = data
        self.text = text
        self.content = content
        self.headers = headers or {}

    def json(self):
        if self._data is None:
            raise ValueError("resposta não é JSON")
        return self._data


def resp_b64(imagem=PNG):
    return FakeResp(200, {"data": [{"b64_json": base64.b64encode(imagem).decode()}]},
                    headers={"Content-Type": "application/json"})


def resp_url(link="https://cdn.exemplo/imagem.png"):
    return FakeResp(200, {"data": [{"url": link}]},
                    headers={"Content-Type": "application/json"})


def resp_bytes(imagem=PNG, mime="image/png"):
    return FakeResp(200, content=imagem, headers={"Content-Type": mime})


def resp_erro(status, mensagem="erro"):
    return FakeResp(status, {"error": mensagem}, text='{"error": "%s"}' % mensagem)


class Rede:
    """Grava as chamadas HTTP e responde pelo que o teste mandar."""

    def __init__(self, responder):
        self.responder = responder
        self.posts = []   # [(url, modelo, headers, json, timeout)]
        self.gets = []    # [(url, timeout)]

    def post(self, url, headers=None, json=None, timeout=None):
        modelo = (json or {}).get("model") or url.split("/models/", 1)[-1]
        self.posts.append({"url": url, "modelo": modelo, "headers": headers,
                           "json": json, "timeout": timeout})
        return self.responder(url, modelo, json or {})

    def get(self, url, timeout=None, **kwargs):
        self.gets.append({"url": url, "timeout": timeout})
        return resp_bytes(JPEG, "image/jpeg")

    @property
    def modelos(self):
        return [p["modelo"] for p in self.posts]


@pytest.fixture
def sleeps(monkeypatch):
    """Não dorme de verdade: só anota quanto tempo teria dormido."""
    anotados = []
    monkeypatch.setattr(ai.time, "sleep", lambda s: anotados.append(s))
    return anotados


@pytest.fixture
def rede(monkeypatch, sleeps):
    """Instala a rede falsa; o teste define o comportamento com `rede.usar(...)`."""
    fake = Rede(lambda url, modelo, payload: resp_b64())
    monkeypatch.setattr(ai.requests, "post", fake.post)
    monkeypatch.setattr(ai.requests, "get", fake.get)
    monkeypatch.setattr(config, "HF_TOKEN", "hf_token_de_teste")
    fake.usar = lambda responder: setattr(fake, "responder", responder)
    return fake


def _confere_higiene(rede):
    """Toda requisição precisa de timeout e da Authorization correta."""
    assert rede.posts, "nenhuma requisição foi feita"
    for p in rede.posts:
        assert p["timeout"] == ai.HF_TIMEOUT, p
        assert p["timeout"] is not None, p
        assert p["headers"]["Authorization"] == "Bearer hf_token_de_teste", p
        assert p["headers"]["Content-Type"] == "application/json", p
    for g in rede.gets:
        assert g["timeout"] == ai.HF_TIMEOUT, g


# --------------------------------------------------------------------------
# sucesso
# --------------------------------------------------------------------------
def test_sucesso_no_primeiro_modelo(rede):
    """Primeiro modelo da cadeia responde b64_json -> devolve os bytes."""
    assert ai.generate_image("um gato astronauta") == PNG

    assert len(rede.posts) == 1
    chamada = rede.posts[0]
    assert chamada["url"] == config.HF_IMAGE_URL == ROTA_NOVA
    assert chamada["json"] == {
        "model": "warp-ai/wuerstchen",
        "prompt": "um gato astronauta",
        "response_format": "b64_json",
    }
    assert chamada["modelo"] == config.HF_IMAGE_MODEL == "warp-ai/wuerstchen"
    assert not rede.gets, "não precisava baixar nada"
    _confere_higiene(rede)


def test_resposta_em_url_baixa_a_imagem(rede):
    """Provedor que devolve `url` em vez de b64: o bot baixa os bytes."""
    rede.usar(lambda url, modelo, payload: resp_url("https://cdn.exemplo/x.jpg"))

    assert ai.generate_image("um dragão") == JPEG
    assert len(rede.gets) == 1
    assert rede.gets[0]["url"] == "https://cdn.exemplo/x.jpg"
    _confere_higiene(rede)


def test_404_na_rota_nova_cai_para_a_rota_antiga(rede):
    """404 na rota nova -> tenta /hf-inference/models/{modelo} (bytes crus)."""
    def responder(url, modelo, payload):
        if url == config.HF_IMAGE_URL:
            return resp_erro(404, "Not Found")
        return resp_bytes(PNG)

    rede.usar(responder)
    assert ai.generate_image("teste") == PNG

    assert len(rede.posts) == 2
    assert rede.posts[0]["url"] == ROTA_NOVA
    assert rede.posts[1]["url"] == ROTA_ANTIGA + "warp-ai/wuerstchen"
    assert rede.posts[1]["json"] == {"inputs": "teste"}
    _confere_higiene(rede)


# --------------------------------------------------------------------------
# cadeia de fallback
# --------------------------------------------------------------------------
def test_cascata_ate_o_flux_schnell(rede):
    """Modelo morto (404/410) derruba a cadeia até o FLUX.1-schnell responder."""
    def responder(url, modelo, payload):
        if modelo == "warp-ai/wuerstchen":
            # 404 nas duas rotas: modelo sem provedor de inferência
            return resp_erro(404, "Model not supported")
        if modelo == "black-forest-labs/FLUX.1-schnell":
            return resp_b64()
        return resp_erro(410, "deprecated")

    rede.usar(responder)
    assert ai.generate_image("um castelo") == PNG

    # wuerstchen tentou rota nova + rota antiga, depois desceu para o schnell
    assert rede.modelos == [
        "warp-ai/wuerstchen",
        "warp-ai/wuerstchen",
        "black-forest-labs/FLUX.1-schnell",
    ], rede.modelos
    assert rede.posts[-1]["url"] == ROTA_NOVA
    assert config.HF_IMAGE_FALLBACKS[0] == "black-forest-labs/FLUX.1-schnell"
    _confere_higiene(rede)


def test_410_desce_sem_gastar_retry(rede):
    """410 não é transitório: nem tenta de novo o mesmo modelo, nem dorme."""
    def responder(url, modelo, payload):
        if modelo == config.HF_IMAGE_MODEL:
            return resp_erro(410, "The requested model is deprecated")
        return resp_b64()

    rede.usar(responder)
    assert ai.generate_image("teste") == PNG
    # 410 (não é 404) nem tenta a rota antiga: desce direto na cadeia
    assert rede.modelos == [config.HF_IMAGE_MODEL,
                            config.HF_IMAGE_FALLBACKS[0]], rede.modelos


def test_cadeia_na_ordem_pedida():
    """A ordem da cadeia é exatamente a que o usuário pediu, com os vivos no fim."""
    cadeia = [config.HF_IMAGE_MODEL] + list(config.HF_IMAGE_FALLBACKS)
    assert cadeia == [
        "warp-ai/wuerstchen",
        "black-forest-labs/FLUX.1-schnell",
        "stabilityai/stable-diffusion-xl-base-1.0",
        "runwayml/stable-diffusion-v1-5",
        "prompthero/openjourney",
        "Qwen/Qwen-Image",
        "Tongyi-MAI/Z-Image-Turbo",
        "black-forest-labs/FLUX.1-dev",
    ]


# --------------------------------------------------------------------------
# erros que o usuário precisa entender
# --------------------------------------------------------------------------
def test_401_fala_da_token(rede):
    """401 vira mensagem sobre a token, e não adianta trocar de modelo."""
    rede.usar(lambda url, modelo, payload: resp_erro(401, "Invalid credentials"))

    with pytest.raises(AIError) as exc:
        ai.generate_image("teste")

    msg = str(exc.value)
    assert "token" in msg.lower()
    assert "huggingface.co/settings/tokens" in msg
    assert len(rede.posts) == 1, "401 não deve descer a cadeia inteira"


def test_403_tambem_fala_da_token(rede):
    rede.usar(lambda url, modelo, payload: resp_erro(403, "Forbidden"))
    with pytest.raises(AIError) as exc:
        ai.generate_image("teste")
    assert "token" in str(exc.value).lower()


def test_402_fala_da_franquia_mensal(rede):
    """402 (ou mensagem de crédito) explica que a franquia da HF acabou."""
    rede.usar(lambda url, modelo, payload: resp_erro(402, "Payment Required"))

    with pytest.raises(AIError) as exc:
        ai.generate_image("teste")

    msg = str(exc.value).lower()
    assert "franquia" in msg and "hugging face" in msg
    assert len(rede.posts) == 1


def test_mensagem_de_credito_com_outro_status(rede):
    """Mesmo em 400, texto de crédito esgotado vira a mensagem da franquia."""
    rede.usar(lambda url, modelo, payload: FakeResp(
        400, {"error": "You have exceeded your monthly included credits"},
        text='{"error":"You have exceeded your monthly included credits"}'))

    with pytest.raises(AIError) as exc:
        ai.generate_image("teste")
    assert "franquia" in str(exc.value).lower()


def test_sem_token_nao_toca_a_rede(monkeypatch):
    """Sem HF_TOKEN falha ANTES de qualquer requisição (prova: rede explode)."""
    def explode(*a, **k):  # pragma: no cover - só existe para falhar o teste
        raise AssertionError("a rede não podia ter sido chamada")

    monkeypatch.setattr(ai.requests, "post", explode)
    monkeypatch.setattr(ai.requests, "get", explode)
    monkeypatch.setattr(config, "HF_TOKEN", "")

    with pytest.raises(AIError) as exc:
        ai.generate_image("teste")

    msg = str(exc.value)
    assert "HF_TOKEN" in msg
    assert "https://huggingface.co/settings/tokens" in msg


def test_prompt_vazio_nao_toca_a_rede(monkeypatch):
    def explode(*a, **k):  # pragma: no cover
        raise AssertionError("a rede não podia ter sido chamada")

    monkeypatch.setattr(ai.requests, "post", explode)
    monkeypatch.setattr(config, "HF_TOKEN", "hf_token_de_teste")
    with pytest.raises(AIError):
        ai.generate_image("   ")


def test_todos_falharem_lista_erros_reais(rede):
    """Cadeia inteira morta -> AIError citando os erros de verdade."""
    rede.usar(lambda url, modelo, payload: resp_erro(410, "no provider"))

    with pytest.raises(AIError) as exc:
        ai.generate_image("teste")

    msg = str(exc.value)
    assert "Nenhum modelo de imagem da Hugging Face respondeu" in msg
    assert "warp-ai/wuerstchen" in msg
    assert "HTTP 410" in msg
    assert "no provider" in msg
    # tentou TODOS os modelos da cadeia, um POST cada (410 não usa rota antiga)
    assert rede.modelos == [config.HF_IMAGE_MODEL] + list(config.HF_IMAGE_FALLBACKS)
    _confere_higiene(rede)


# --------------------------------------------------------------------------
# retry / backoff
# --------------------------------------------------------------------------
def test_503_respeita_estimated_time_com_teto(rede, sleeps):
    """503 espera o estimated_time da resposta, limitado a 30s."""
    respostas = [
        FakeResp(503, {"error": "loading", "estimated_time": 12.0}),
        FakeResp(503, {"error": "loading", "estimated_time": 900.0}),
        resp_b64(),
    ]
    rede.usar(lambda url, modelo, payload: respostas.pop(0))

    assert ai.generate_image("teste") == PNG
    assert sleeps == [12.0, 30]
    assert ai.HF_MAX_WAIT == 30
    assert len(rede.posts) == 3
    assert set(rede.modelos) == {config.HF_IMAGE_MODEL}, "503 não troca de modelo"


def test_500_retenta_com_backoff_exponencial(rede, sleeps):
    """Erro transitório retenta no mesmo modelo com backoff 1, 2, 4..."""
    respostas = [resp_erro(500, "boom"), resp_erro(502, "boom"), resp_b64()]
    rede.usar(lambda url, modelo, payload: respostas.pop(0))

    assert ai.generate_image("teste") == PNG
    assert sleeps == [1, 2]
    assert set(rede.modelos) == {config.HF_IMAGE_MODEL}


def test_erro_de_rede_retenta_e_depois_desce(rede, sleeps):
    """Falha de rede é transitória: retenta até o limite e só então desce."""
    tentativas = {"n": 0}

    def responder(url, modelo, payload):
        tentativas["n"] += 1
        if modelo == config.HF_IMAGE_MODEL:
            raise ai.requests.RequestException("conexão caiu")
        return resp_b64()

    rede.usar(responder)
    assert ai.generate_image("teste") == PNG
    # 4 tentativas no primeiro modelo e só então a cadeia desce
    assert rede.modelos[:ai.HF_MAX_RETRIES] == [config.HF_IMAGE_MODEL] * ai.HF_MAX_RETRIES
    assert len(rede.posts) == ai.HF_MAX_RETRIES + 1, rede.modelos
    assert rede.modelos[-1] == config.HF_IMAGE_FALLBACKS[0]
    assert sleeps, "deveria ter dormido entre as tentativas"


def test_200_sem_imagem_retenta_e_depois_desce(rede, sleeps):
    """200 com JSON sem imagem não engana: retenta e depois troca de modelo."""
    def responder(url, modelo, payload):
        if modelo == config.HF_IMAGE_MODEL:
            return FakeResp(200, {"data": []},
                            headers={"Content-Type": "application/json"})
        return resp_b64()

    rede.usar(responder)
    assert ai.generate_image("teste") == PNG
    assert rede.modelos[-1] == config.HF_IMAGE_FALLBACKS[0]
