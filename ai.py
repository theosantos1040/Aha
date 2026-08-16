"""Cliente de IA do ThzyxBoTS usando a API do OpenRouter.

Características:
- Suporta vários modelos gratuitos (testados com HTTP 200).
- Retenta automaticamente em 429/5xx/timeout com backoff exponencial.
- Faz fallback do campo `content` para `reasoning` (alguns modelos free
  só preenchem `reasoning`, ex.: z-ai/glm-4.5-air).
- Pode cair para outro modelo da lista se o escolhido continuar falhando.
"""
import base64
import io
import time
import requests

import config

DEFAULT_TIMEOUT = 120  # nex-agi pode demorar bastante
MAX_RETRIES = 4


class AIError(Exception):
    pass


def _system_prompt(mode: str = None, name: str = None, bio: str = None) -> str:
    name = name or config.BOT_NAME
    base = (
        f"Você é {name}, uma assistente de IA que conversa em português dentro "
        f"do WhatsApp. Responda de forma clara e útil. "
    )
    mode = (mode or config.DEFAULT_AI_MODE).lower()
    base += config.AI_MODES.get(mode, config.AI_MODES[config.DEFAULT_AI_MODE])
    if bio:
        base += f" Informação extra sobre você: {bio}"
    return base


def _extract(data: dict) -> str:
    """Extrai texto da resposta, com fallback para 'reasoning'."""
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return ""
    content = (msg.get("content") or "").strip()
    if content:
        return content
    # alguns modelos free só preenchem reasoning
    reasoning = (msg.get("reasoning") or "").strip()
    return reasoning


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/thzyxbots",
        "X-Title": config.BOT_NAME,
    }


def _request(payload: dict, timeout: int, want) -> object:
    """POST no OpenRouter com retry/backoff.

    `want(data)` extrai o resultado desejado do JSON e devolve algo "falsy"
    quando a resposta veio vazia (aí vale a pena tentar de novo).
    """
    if not config.OPENROUTER_API_KEY:
        raise AIError("OPENROUTER_API_KEY não configurada (.env).")

    last_err = ""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                config.OPENROUTER_URL, headers=_headers(), json=payload, timeout=timeout
            )
        except requests.RequestException as exc:
            last_err = f"rede: {exc}"
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 200:
            data = resp.json()
            # erros vêm às vezes com 200 + {"error": ...}
            if "error" in data and not data.get("choices"):
                last_err = str(data["error"])
                time.sleep(2 ** attempt)
                continue
            result = want(data)
            if result:
                return result
            last_err = "resposta vazia"
            time.sleep(2 ** attempt)
            continue

        if resp.status_code in (429, 500, 502, 503, 504):
            last_err = f"HTTP {resp.status_code}"
            try:
                retry_after = float(
                    resp.json().get("error", {}).get("metadata", {})
                    .get("retry_after_seconds", 2 ** attempt)
                )
            except Exception:
                retry_after = 2 ** attempt
            time.sleep(min(retry_after, 20))
            continue

        # erro não recuperável (ex.: 400, 401, 402)
        raise AIError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:200]}")

    raise AIError(f"Falha após {MAX_RETRIES} tentativas ({last_err})")


def _call_model(model_id: str, messages: list, timeout: int) -> str:
    payload = {"model": model_id, "messages": messages, "max_tokens": 800}
    return _request(payload, timeout, _extract)


def chat(prompt: str, model_key: str = None, history: list = None,
         mode: str = None, name: str = None, bio: str = None) -> str:
    """Conversa com a IA.

    model_key: chave amigável em config.AI_MODELS (chatgpt/nex/glm/gemini).
    mode: personalidade (carinhosa/zoeira/sincera).
    name/bio: nome e descrição personalizados da IA no grupo.
    history: lista opcional de mensagens [{role, content}, ...].
    """
    if not config.OPENROUTER_API_KEY:
        raise AIError("OPENROUTER_API_KEY não configurada (.env).")

    model_key = (model_key or config.DEFAULT_AI_MODEL).lower()
    model_id = config.AI_MODELS.get(model_key, config.AI_MODELS[config.DEFAULT_AI_MODEL])

    messages = [{"role": "system", "content": _system_prompt(mode, name, bio)}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    # tenta o modelo pedido; se falhar de vez, cai para os outros
    tried = [model_id]
    try:
        return _call_model(model_id, messages, DEFAULT_TIMEOUT)
    except AIError:
        for key, mid in config.AI_MODELS.items():
            if mid in tried:
                continue
            tried.append(mid)
            try:
                return _call_model(mid, messages, DEFAULT_TIMEOUT)
            except AIError:
                continue
    raise AIError("Todos os modelos de IA falharam no momento. Tente novamente.")


# ===================== VISÃO (analisar imagem) =====================
def _mime_for(kind: str, data: bytes) -> str:
    """Descobre o mime pelos magic bytes (o WhatsApp manda jpeg/png/webp)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def vision(prompt: str, image_bytes: bytes, mode: str = None,
           name: str = None, bio: str = None) -> str:
    """Analisa uma IMAGEM e responde em texto.

    Usa um modelo com visão de verdade (input_modalities inclui "image").
    Se o principal falhar, tenta os fallbacks — todos verificados no
    catálogo do OpenRouter como capazes de receber imagem.
    """
    if not image_bytes:
        raise AIError("Nenhuma imagem recebida para analisar.")

    b64 = base64.b64encode(image_bytes).decode()
    mime = _mime_for("image", image_bytes)
    messages = [
        {"role": "system", "content": _system_prompt(mode, name, bio)},
        {"role": "user", "content": [
            {"type": "text", "text": prompt or "Descreva esta imagem em detalhes."},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]},
    ]

    erros = []
    for model_id in [config.AI_VISION_MODEL] + list(config.AI_VISION_FALLBACKS):
        try:
            return _call_model(model_id, messages, DEFAULT_TIMEOUT)
        except AIError as exc:
            erros.append(f"{model_id}: {exc}")
            continue
    raise AIError("Nenhum modelo de visão respondeu. " + " | ".join(erros[:2]))


# ===================== PESQUISA =====================
def search(query: str) -> str:
    """Responde uma pergunta de pesquisa de forma organizada."""
    if not query.strip():
        raise AIError("Diga o que devo pesquisar.")
    messages = [
        {"role": "system", "content": (
            "Você é um assistente de pesquisa em português. Responda de forma "
            "organizada e objetiva, com os pontos principais em tópicos curtos. "
            "Se não tiver certeza de algo, diga explicitamente que não tem "
            "certeza — nunca invente fatos, datas ou números."
        )},
        {"role": "user", "content": query},
    ]
    return _call_model(config.AI_SEARCH_MODEL, messages, DEFAULT_TIMEOUT)


# ===================== TRANSCRIÇÃO DE ÁUDIO =====================
def _audio_format(data: bytes) -> str:
    """Descobre o formato pelos magic bytes. '' quando precisa converter."""
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    return ""  # OGG/Opus (padrão do WhatsApp), M4A etc.


def transcribe(audio_bytes: bytes, language: str = "pt") -> str:
    """Transcreve um áudio para texto.

    O áudio de voz do WhatsApp é OGG/Opus, mas a API só aceita mp3/wav, então
    convertemos com o ffmpeg antes de enviar. A conversão é importada aqui
    dentro para o ai.py não depender do media.py no import.
    """
    if not audio_bytes:
        raise AIError("Nenhum áudio recebido para transcrever.")

    fmt = _audio_format(audio_bytes)
    if not fmt:
        try:
            import media
            audio_bytes = media.to_mp3(audio_bytes)
            fmt = "mp3"
        except Exception as exc:
            raise AIError(f"não consegui preparar o áudio: {exc}") from exc

    b64 = base64.b64encode(audio_bytes).decode()
    messages = [
        {"role": "system", "content": (
            "Você transcreve áudios com precisão. Responda APENAS com a "
            "transcrição literal do que foi falado, sem comentários, sem "
            "resumo e sem tradução. Se não houver fala audível, responda "
            "exatamente: [sem fala audível]"
        )},
        {"role": "user", "content": [
            {"type": "text", "text": f"Transcreva este áudio (idioma: {language})."},
            {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}},
        ]},
    ]

    erros = []
    for model_id in [config.AI_AUDIO_MODEL] + list(config.AI_AUDIO_FALLBACKS):
        try:
            return _call_model(model_id, messages, DEFAULT_TIMEOUT)
        except AIError as exc:
            erros.append(f"{model_id}: {exc}")
            continue
    raise AIError("Nenhum modelo conseguiu transcrever. " + " | ".join(erros[:2]))


# ===================== GERAÇÃO DE IMAGEM (Hugging Face) =====================
# O /gerarimagem usa exclusivamente o cliente oficial da Hugging Face. Isso é
# importante porque o roteamento de text-to-image varia entre os provedores e
# não é coberto pelo endpoint OpenAI-compatible de chat.
HF_TIMEOUT = 120
HF_TOTAL_TIMEOUT = 180
HF_MAX_RETRIES = 2
HF_MAX_WAIT = 30
HF_MAX_PROMPT_CHARS = 1000
HF_MAX_IMAGE_BYTES = 10 * 1024 * 1024
HF_MAX_IMAGE_PIXELS = 16_000_000

HF_TOKEN_HELP = (
    "Configure HF_TOKEN no .env (ou no painel do Render) com uma token "
    "fine-grained criada em https://huggingface.co/settings/tokens e habilite "
    "a permissão 'Make calls to Inference Providers'."
)


class _ModeloIndisponivel(AIError):
    """Falha limitada a um modelo; o próximo fallback ainda pode funcionar."""


def _create_hf_client(timeout: float = HF_TIMEOUT):
    """Cria o cliente oficial sem tornar a dependência obrigatória no import."""
    try:
        from huggingface_hub import InferenceClient
    except ImportError as exc:  # pragma: no cover - falha de instalação
        raise AIError(
            "A dependência huggingface_hub não está instalada. "
            "Execute: pip install -r requirements.txt"
        ) from exc
    return InferenceClient(
        provider="auto", api_key=config.HF_TOKEN, timeout=max(1, timeout)
    )


def _sao_bytes_de_imagem(dados: bytes) -> bool:
    """Valida PNG, JPEG ou WEBP pelos magic bytes."""
    if not dados:
        return False
    return (
        dados.startswith(b"\x89PNG\r\n\x1a\n")
        or dados.startswith(b"\xff\xd8\xff")
        or (dados.startswith(b"RIFF") and dados[8:12] == b"WEBP")
    )


def _hf_image_bytes(image) -> bytes:
    """Normaliza a imagem do SDK para bytes seguros para o WhatsApp."""
    if isinstance(image, (bytes, bytearray, memoryview)):
        data = bytes(image)
    elif hasattr(image, "save"):
        try:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > HF_MAX_IMAGE_PIXELS:
                raise _ModeloIndisponivel(
                    "a imagem gerada excedeu o limite de 16 megapixels"
                )
        except (AttributeError, TypeError, ValueError):
            pass
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
    else:
        raise _ModeloIndisponivel("a Hugging Face devolveu um formato desconhecido")

    if len(data) > HF_MAX_IMAGE_BYTES:
        raise _ModeloIndisponivel("a imagem gerada excedeu o limite de 10 MB")
    if not _sao_bytes_de_imagem(data):
        raise _ModeloIndisponivel("a resposta não contém uma imagem válida")
    return data


def _hf_status(exc) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _hf_retry_delay(exc, attempt: int) -> float:
    """Backoff limitado; respeita Retry-After/estimated_time quando presentes."""
    delay = 2 ** attempt
    response = getattr(exc, "response", None)
    try:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            delay = float(retry_after)
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        estimated = float(response.json().get("estimated_time", 0))
        if estimated > 0:
            delay = estimated
    except (AttributeError, TypeError, ValueError):
        pass
    return min(max(delay, 0), HF_MAX_WAIT)


def _hf_account_error(exc) -> AIError | None:
    """Converte erros de token/crédito em mensagens acionáveis e sem segredo."""
    status = _hf_status(exc)
    message = str(exc).lower()
    auth_words = ("token", "auth", "credential", "permission")
    if status == 401 or (
        status == 403 and any(word in message for word in auth_words)
    ):
        return AIError(
            f"Hugging Face recusou a token (HTTP {status}). Ela é inválida ou "
            f"não tem permissão de inferência. {HF_TOKEN_HELP}"
        )
    if status == 402 or any(word in message for word in ("credit", "quota", "billing")):
        return AIError(
            "Os créditos de inferência da conta Hugging Face acabaram. "
            "Consulte https://huggingface.co/settings/billing."
        )
    return None


def _hf_is_transient(exc) -> bool:
    status = _hf_status(exc)
    if status in (408, 409, 425, 429, 500, 502, 503, 504):
        return True
    name = type(exc).__name__.lower()
    return status is None and any(
        part in name for part in ("timeout", "connection", "connect", "network")
    )


def _hf_image_model(model: str, prompt: str, deadline: float) -> bytes:
    """Tenta um modelo, com retry apenas para erros realmente transitórios."""
    last_error = ""
    for attempt in range(HF_MAX_RETRIES):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AIError(
                "A geração de imagem excedeu o tempo total de 180 segundos."
            )
        try:
            client = _create_hf_client(min(HF_TIMEOUT, remaining))
            image = client.text_to_image(prompt, model=model)
            return _hf_image_bytes(image)
        except _ModeloIndisponivel:
            raise
        except AIError:
            raise
        except Exception as exc:
            account_error = _hf_account_error(exc)
            if account_error:
                raise account_error from exc

            status = _hf_status(exc)
            detail = str(exc).strip().replace("\n", " ")[:180]
            if config.HF_TOKEN:
                detail = detail.replace(config.HF_TOKEN, "[redacted]")
            last_error = f"HTTP {status}: {detail}" if status else detail
            if not _hf_is_transient(exc):
                raise _ModeloIndisponivel(last_error or type(exc).__name__) from exc
            if attempt + 1 < HF_MAX_RETRIES:
                remaining = deadline - time.monotonic()
                delay = min(_hf_retry_delay(exc, attempt), max(remaining, 0))
                if delay <= 0:
                    raise AIError(
                        "A geração de imagem excedeu o tempo total de 180 segundos."
                    ) from exc
                time.sleep(delay)

    raise _ModeloIndisponivel(
        f"falhou após {HF_MAX_RETRIES} tentativas ({last_error or 'erro transitório'})"
    )


def generate_image(prompt: str) -> bytes:
    """Gera imagem somente via HF, seguindo a cadeia fixa configurada."""
    prompt = prompt.strip()
    if not prompt:
        raise AIError("Descreva a imagem que devo gerar.")
    if len(prompt) > HF_MAX_PROMPT_CHARS:
        raise AIError(
            f"A descrição é longa demais (máximo: {HF_MAX_PROMPT_CHARS} caracteres)."
        )
    if not config.HF_TOKEN:
        raise AIError(
            "A geração de imagem usa a Hugging Face e falta a HF_TOKEN. "
            + HF_TOKEN_HELP
        )

    deadline = time.monotonic() + HF_TOTAL_TIMEOUT
    errors = []
    for model in config.HF_IMAGE_MODELS:
        try:
            return _hf_image_model(model, prompt, deadline)
        except _ModeloIndisponivel as exc:
            errors.append(f"{model}: {exc}")

    details = " | ".join(errors[:2])
    raise AIError(
        "Nenhum modelo de imagem da Hugging Face respondeu agora. " + details
    )
