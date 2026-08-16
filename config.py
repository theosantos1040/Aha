"""Configuração central do ThzyxBoTS."""
import os

# guarda de onde a chave foi carregada (útil para diagnóstico)
ENV_LOADED_FROM = None


def _candidate_env_paths():
    """Possíveis locais do .env: pasta do script, diretório atual e o pai."""
    here = os.path.dirname(os.path.abspath(__file__))
    seen = []
    for p in (
        os.path.join(here, ".env"),
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(here), ".env"),
    ):
        if p not in seen:
            seen.append(p)
    return seen


def _clean_value(value: str) -> str:
    """Limpa o valor: aspas, espaços, comentário inline e caracteres invisíveis."""
    value = value.strip()
    # remove aspas envolventes
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1]
    # remove BOM e zero-width que o nano/copiar-colar às vezes inserem
    for junk in ("﻿", "​", "‎", "‏", "\xa0"):
        value = value.replace(junk, "")
    return value.strip()


def _load_env_manual():
    """Lê o .env sem depender do python-dotenv (comum faltar no Termux).

    Robusto a: BOM, CRLF, 'export KEY=', aspas, espaços e caminhos diferentes.
    """
    global ENV_LOADED_FROM
    for env_path in _candidate_env_paths():
        if not os.path.exists(env_path):
            continue
        try:
            # utf-8-sig remove BOM automaticamente
            with open(env_path, "r", encoding="utf-8-sig") as fh:
                for raw in fh:
                    line = raw.strip().lstrip("﻿")
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.lower().startswith("export "):
                        line = line[7:]
                    key, _, value = line.partition("=")
                    key = key.strip().lstrip("﻿")
                    value = _clean_value(value)
                    if key:
                        # sobrescreve para garantir o valor do .env
                        os.environ[key] = value
            ENV_LOADED_FROM = env_path
            return
        except Exception:
            continue


# 1) tenta o python-dotenv (se instalado); 2) fallback manual sempre roda
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv é opcional em runtime
    pass
_load_env_manual()

BOT_NAME = os.getenv("BOT_NAME", "ThzyxBoTS")
DEFAULT_PREFIX = os.getenv("DEFAULT_PREFIX", "/")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ---- Hugging Face ------------------------------------------------------
# Token da Hugging Face, usada pelo /gerarimagem (é a ÚNICA fonte de imagem
# agora — o OpenRouter foi abandonado nesse comando porque cobra créditos).
# A token NUNCA vai no código: defina no .env local ou no painel do Render.
# Pegue a sua em https://huggingface.co/settings/tokens e marque a permissão
# "Make calls to Inference Providers". Sem token, a HF responde HTTP 401.
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()

SESSION_DB = os.getenv("SESSION_DB", "session.sqlite3")
DATA_DB = os.getenv("DATA_DB", "bot_data.sqlite3")

OWNERS = [o.strip() for o in os.getenv("OWNERS", "").split(",") if o.strip()]

# Número que recebe o código de pareamento automaticamente ao subir o bot.
# Só dígitos, com DDI+DDD. Pode ser trocado pela variável de ambiente
# PHONE_NUMBER (útil no Render/Termux sem mexer no código).
PHONE_NUMBER = "".join(c for c in os.getenv("PHONE_NUMBER", "5524992506307") if c.isdigit())

# Modelos de IA disponíveis no comando /IA.
# Foram testados de verdade contra o OpenRouter (HTTP 200).
# Chave amigável -> id real do modelo.
AI_MODELS = {
    "chatgpt": "openai/gpt-oss-120b:free",   # rápido e estável (padrão)
    "nex": "nex-agi/nex-n2-pro:free",         # existe, porém mais lento
    "glm": "z-ai/glm-4.5-air:free",           # devolve texto em "reasoning"
    "gemini": "google/gemma-4-31b-it:free",   # modelo do Google (Gemma; Gemini free não existe)
    "ling": "inclusionai/ling-3.0-flash:free",  # texto (NÃO aceita imagem)
}
DEFAULT_AI_MODEL = "chatgpt"

# ---- Modelos especializados -------------------------------------------
# Todos conferidos no catálogo público do OpenRouter (/api/v1/models).
#
# VISÃO (/analiseia): precisa de "image" em input_modalities. O
# inclusionai/ling-3.0-flash:free existe mas é SÓ TEXTO, então não serve
# aqui — estes abaixo aceitam imagem de verdade e são gratuitos.
AI_VISION_MODEL = os.getenv("AI_VISION_MODEL", "nvidia/nemotron-nano-12b-v2-vl:free")
AI_VISION_FALLBACKS = [
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

# PESQUISA (/pesquisa) — modelo de texto.
AI_SEARCH_MODEL = os.getenv("AI_SEARCH_MODEL", "openai/gpt-oss-20b:free")

# TRANSCRIÇÃO DE ÁUDIO (/transcrever) — precisa de "audio" em input_modalities.
# Conferido no catálogo do OpenRouter: dos 38 modelos que aceitam áudio, este é
# o ÚNICO gratuito. Os fallbacks consomem créditos, mas são os mais baratos.
AI_AUDIO_MODEL = os.getenv(
    "AI_AUDIO_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
)
AI_AUDIO_FALLBACKS = [
    "google/gemini-2.5-flash-lite",
    "mistralai/voxtral-small-24b-2507",
]

# GERAÇÃO DE IMAGEM antiga (OpenRouter). MANTIDO SÓ POR COMPATIBILIDADE:
# o /gerarimagem NÃO usa mais isso, porque o OpenRouter cobra créditos para
# gerar imagem. Hoje o comando usa exclusivamente a Hugging Face (HF_* abaixo).
AI_IMAGE_MODEL = os.getenv("AI_IMAGE_MODEL", "google/gemini-2.5-flash-image")

# ---- GERAÇÃO DE IMAGEM (/gerarimagem) — Hugging Face --------------------
# Rota principal (auto-roteamento, compatível com a API da OpenAI):
#   POST https://router.huggingface.co/v1/images/generations
#   body {"model": "<id>", "prompt": "<texto>", "response_format": "b64_json"}
# A própria HF escolhe um provedor VIVO para o modelo (fal-ai, replicate,
# nscale, together, wavespeed...). A resposta é JSON e a imagem vem em
# data[0].b64_json OU em data[0].url — o ai.py trata os dois casos.
#
# NÃO use mais a rota antiga /hf-inference/models/{modelo}: o provedor
# "hf-inference" hoje serve UM único modelo de texto-para-imagem
# (stabilityai/stable-diffusion-3-medium-diffusers), nenhum da cadeia abaixo.
# Ela continua como último recurso no ai.py, caso a rota nova devolva 404.
#
# A cadeia começa em HF_IMAGE_MODEL e desce por HF_IMAGE_FALLBACKS até algum
# modelo responder. A ORDEM dos 5 primeiros é a pedida pelo usuário e está
# mantida de propósito, mesmo sabendo que 3 deles NÃO funcionam mais:
#
#   1. warp-ai/wuerstchen ....................... MORTO — sem provedor de
#      inferência (campo inferenceProviderMapping vazio na API da HF).
#   2. black-forest-labs/FLUX.1-schnell ......... VIVO (nscale, fal-ai,
#      together, wavespeed).
#   3. stabilityai/stable-diffusion-xl-base-1.0 . VIVO (fal-ai).
#   4. runwayml/stable-diffusion-v1-5 ........... MORTO — sem provedor.
#   5. prompthero/openjourney ................... MORTO — sem provedor.
#
# Os mortos respondem 404/410/400 "not supported" na hora, então custam
# quase nada: a cadeia desce para o próximo sem gastar retry. Ficam na lista
# só porque foram pedidos e porque podem voltar a ter provedor um dia.
# Os três últimos foram conferidos como VIVOS e existem para o comando de
# fato funcionar quando os anteriores falharem.
HF_IMAGE_URL = os.getenv(
    "HF_IMAGE_URL", "https://router.huggingface.co/v1/images/generations"
)
HF_IMAGE_MODEL = os.getenv("HF_IMAGE_MODEL", "warp-ai/wuerstchen")
HF_IMAGE_FALLBACKS = [
    "black-forest-labs/FLUX.1-schnell",          # vivo
    "stabilityai/stable-diffusion-xl-base-1.0",  # vivo
    "runwayml/stable-diffusion-v1-5",            # morto (sem provedor)
    "prompthero/openjourney",                    # morto (sem provedor)
    # --- confirmados vivos, garantem que o comando responda ---
    "Qwen/Qwen-Image",          # fal-ai, replicate, wavespeed
    "Tongyi-MAI/Z-Image-Turbo",  # fal-ai, replicate, wavespeed
    "black-forest-labs/FLUX.1-dev",  # fal-ai, replicate, wavespeed
]

# Personalidades da IA por grupo (/iamode). Cada modo injeta um trecho no
# system prompt para mudar o tom das respostas.
AI_MODES = {
    "carinhosa": (
        "Sua personalidade é CARINHOSA: responda de forma amigável e educada, "
        "faça elogios quando apropriado e mantenha sempre um clima positivo. "
        "Use MUITOS emojis fofos ✨🥰💖🌸."
    ),
    "zoeira": (
        "Sua personalidade é ZOEIRA: faça piadas leves, brinque com os usuários "
        "e seja divertida — mas NUNCA ofenda, humilhe ou use palavras pesadas. "
        "Use emojis engraçados 😆🤣🔥😜."
    ),
    "sincera": (
        "Sua personalidade é SINCERA: vá direto ao ponto, use POUCOS emojis e "
        "enfeites, dê opiniões objetivas e foque na utilidade da resposta."
    ),
}
DEFAULT_AI_MODE = "carinhosa"

# Recompensas / economia
DAILY_REWARD = 250
START_BALANCE = 100

# XP por mensagem
XP_PER_MESSAGE = 10

# Símbolos decorativos do bot 🌸
DECO_TOP = "𓊆ྀི❤︎𓊇 ◡̈"
DECO_LINE = "•︡ᯅ•︠ ────────── •︡ᯅ•︠"
DECO_NAME = f"𓊆ྀི {BOT_NAME} ❤︎𓊇 ◡̈"
