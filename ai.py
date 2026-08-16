"""Cliente de IA do ThzyxBoTS usando a API do OpenRouter.

Características:
- Suporta vários modelos gratuitos (testados com HTTP 200).
- Retenta automaticamente em 429/5xx/timeout com backoff exponencial.
- Faz fallback do campo `content` para `reasoning` (alguns modelos free
  só preenchem `reasoning`, ex.: z-ai/glm-4.5-air).
- Pode cair para outro modelo da lista se o escolhido continuar falhando.
"""
import base64
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
# O /gerarimagem usa EXCLUSIVAMENTE a Hugging Face. O OpenRouter foi deixado
# de fora porque lá geração de imagem cobra créditos da conta.
HF_TIMEOUT = 120          # modelos de imagem demoram; nunca ficar sem timeout
HF_MAX_RETRIES = 4        # só para erro transitório (429/5xx/rede)
HF_MAX_WAIT = 30          # teto de espera do "modelo carregando" (503)

HF_TOKEN_HELP = (
    "Configure HF_TOKEN no .env (ou no painel do Render) com uma token "
    "criada em https://huggingface.co/settings/tokens — marque a permissão "
    "'Make calls to Inference Providers'."
)


class _ModeloIndisponivel(AIError):
    """Modelo saiu do ar/não tem provedor: dá para tentar o próximo da cadeia.

    É subclasse de AIError de propósito: se vazar para quem chamou, continua
    sendo o mesmo tipo de erro que o bot.py já trata.
    """


def _hf_legacy_url(model: str) -> str:
    """Rota ANTIGA do router (200 = bytes crus da imagem).

    Só é usada como último recurso: hoje o provedor "hf-inference" serve
    praticamente nenhum dos modelos da cadeia, então a rota nova
    (config.HF_IMAGE_URL, com auto-roteamento) vem primeiro.
    """
    return f"https://router.huggingface.co/hf-inference/models/{model}"


def _hf_headers() -> dict:
    return {
        "Authorization": f"Bearer {config.HF_TOKEN}",
        "Content-Type": "application/json",
    }


def _hf_espera_carregando(resp, attempt: int) -> float:
    """Quanto esperar num HTTP 503 ('modelo carregando'), com teto."""
    espera = 2 ** attempt
    try:
        estimado = float(resp.json().get("estimated_time", 0))
        if estimado > 0:
            espera = estimado
    except Exception:
        pass
    return min(espera, HF_MAX_WAIT)


def _sao_bytes_de_imagem(dados: bytes) -> bool:
    """Magic bytes de PNG / JPEG / WEBP — confere se veio imagem mesmo."""
    return bool(dados) and dados[:8].startswith(
        (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")
    )


def _hf_baixa_url(url: str) -> bytes:
    """Baixa a imagem quando o provedor devolve URL em vez de base64."""
    try:
        resp = requests.get(url, timeout=HF_TIMEOUT)
    except requests.RequestException:
        return b""
    if resp.status_code != 200:
        return b""
    if "image" in (resp.headers.get("Content-Type") or ""):
        return resp.content
    return resp.content if _sao_bytes_de_imagem(resp.content) else b""


def _hf_extrai_json(resp) -> bytes:
    """Extrai a imagem da resposta da rota nova (formato estilo OpenAI).

    O formato varia por provedor: uns mandam `b64_json`, outros só uma `url`.
    Devolve b"" quando não deu para extrair (aí vale a pena retentar).
    """
    try:
        dados = resp.json()
    except Exception:
        return b""
    itens = dados.get("data") or dados.get("images") or []
    if isinstance(itens, dict):
        itens = [itens]
    for item in itens:
        if not isinstance(item, dict):
            continue
        b64 = item.get("b64_json") or item.get("image_base64")
        if b64:
            try:
                return base64.b64decode(b64)
            except Exception:
                continue
        url = item.get("url")
        if url:
            baixado = _hf_baixa_url(url)
            if baixado:
                return baixado
    return b""


def _hf_extrai_bytes(resp) -> bytes:
    """Extrai a imagem da rota antiga, que devolve os bytes crus."""
    if "image" in (resp.headers.get("Content-Type") or ""):
        return resp.content
    # às vezes o Content-Type vem errado: confere pelos magic bytes
    return resp.content if _sao_bytes_de_imagem(resp.content) else b""


def _hf_post_imagem(url: str, payload: dict, extrair) -> bytes:
    """POST na Hugging Face com retry/backoff, devolvendo os bytes da imagem.

    `extrair(resp)` tira a imagem do HTTP 200 e devolve b"" se não achou.
    Levanta `_ModeloIndisponivel` quando não adianta insistir NESSA rota/modelo
    (a cadeia deve descer) e `AIError` quando o problema é da conta
    (token/franquia) — aí trocar de modelo não resolve nada.
    """
    ultimo_erro = ""
    for attempt in range(HF_MAX_RETRIES):
        try:
            resp = requests.post(
                url, headers=_hf_headers(), json=payload, timeout=HF_TIMEOUT,
            )
        except requests.RequestException as exc:
            ultimo_erro = f"rede: {exc}"
            time.sleep(2 ** attempt)
            continue

        status = resp.status_code

        if status == 200:
            imagem = extrair(resp)
            if imagem:
                return imagem
            # 200 sem imagem costuma ser JSON de erro disfarçado
            ultimo_erro = "a resposta veio sem imagem"
            time.sleep(2 ** attempt)
            continue

        corpo = (resp.text or "")[:200]
        corpo_baixo = corpo.lower()

        # --- problemas da CONTA: trocar de modelo não resolve ---
        if status == 402 or "credit" in corpo_baixo or "quota" in corpo_baixo:
            raise AIError(
                "A franquia mensal de inferência da sua conta Hugging Face "
                "acabou. Espere renovar no próximo mês ou assine o PRO em "
                "https://huggingface.co/settings/billing."
            )
        if status in (401, 403):
            raise AIError(
                f"Hugging Face recusou a token (HTTP {status}): ela é inválida "
                f"ou não tem permissão de inferência. {HF_TOKEN_HELP}"
            )

        # --- modelo carregando: respeita o estimated_time (teto de 30s) ---
        if status == 503:
            ultimo_erro = "modelo carregando (HTTP 503)"
            time.sleep(_hf_espera_carregando(resp, attempt))
            continue

        # --- transitórios: vale a pena retentar ---
        if status in (429, 500, 502, 504):
            ultimo_erro = f"HTTP {status}"
            time.sleep(2 ** attempt)
            continue

        # --- modelo morto/inexistente: desce a cadeia SEM gastar retry ---
        raise _ModeloIndisponivel(f"HTTP {status}: {corpo}")

    raise _ModeloIndisponivel(
        f"falhou após {HF_MAX_RETRIES} tentativas ({ultimo_erro})"
    )


def _hf_image_model(model: str, prompt: str) -> bytes:
    """Gera a imagem num modelo específico da HF.

    Tenta primeiro a rota com AUTO-ROTEAMENTO (config.HF_IMAGE_URL), que deixa
    a própria HF escolher um provedor vivo. Se ela responder 404 (rota ou
    modelo desconhecido), tenta a rota antiga /hf-inference/models/{modelo},
    que devolve os bytes crus.
    """
    try:
        return _hf_post_imagem(
            config.HF_IMAGE_URL,
            {"model": model, "prompt": prompt, "response_format": "b64_json"},
            _hf_extrai_json,
        )
    except _ModeloIndisponivel as exc:
        if "HTTP 404" not in str(exc):
            raise
        erro_rota_nova = exc

    try:
        return _hf_post_imagem(
            _hf_legacy_url(model), {"inputs": prompt}, _hf_extrai_bytes
        )
    except _ModeloIndisponivel as exc:
        raise _ModeloIndisponivel(
            f"{erro_rota_nova} (rota antiga hf-inference: {exc})"
        ) from exc


def generate_image(prompt: str) -> bytes:
    """Gera uma imagem a partir de um texto e devolve os bytes (PNG/JPEG).

    Usa SÓ a Hugging Face: começa em config.HF_IMAGE_MODEL e desce por
    config.HF_IMAGE_FALLBACKS enquanto os modelos estiverem indisponíveis.
    """
    if not prompt.strip():
        raise AIError("Descreva a imagem que devo gerar.")
    if not config.HF_TOKEN:
        raise AIError(
            "A geração de imagem usa a Hugging Face e falta a HF_TOKEN. "
            + HF_TOKEN_HELP
        )

    principal = config.HF_IMAGE_MODEL
    candidatos = [principal] + [
        m for m in config.HF_IMAGE_FALLBACKS if m != principal
    ]

    erros = []
    for modelo in candidatos:
        try:
            return _hf_image_model(modelo, prompt)
        except _ModeloIndisponivel as exc:
            erros.append(f"{modelo}: {exc}")
            continue  # modelo fora do ar: tenta o próximo da cadeia

    raise AIError(
        "Nenhum modelo de imagem da Hugging Face respondeu agora. "
        + " | ".join(erros[:2])
    )
