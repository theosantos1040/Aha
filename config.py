"""Configuração central do ThzyxBoTS."""
import os

# guarda de onde a chave foi carregada (útil para diagnóstico)
ENV_LOADED_FROM = None


def _candidate_env_paths():
    """Locais explícitos do projeto; nunca lê um .env do diretório pai."""
    here = os.path.dirname(os.path.abspath(__file__))
    seen = []
    for p in (
        os.path.join(here, ".env"),
        os.path.join(os.getcwd(), ".env"),
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
    elif " #" in value:
        value = value.split(" #", 1)[0].rstrip()
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
                        # Variáveis injetadas pelo host/shell sempre têm prioridade.
                        os.environ.setdefault(key, value)
            ENV_LOADED_FROM = env_path
            return
        except Exception:
            continue


# 1) tenta o python-dotenv (se instalado); 2) fallback manual sempre roda
try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=_candidate_env_paths()[0], override=False)
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
# Só dígitos, com DDI+DDD. Nunca mantenha um número real como padrão no código.
PHONE_NUMBER = "".join(c for c in os.getenv("PHONE_NUMBER", "") if c.isdigit())

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

# ---- GERAÇÃO DE IMAGEM (/gerarimagem) — Hugging Face --------------------
# O ai.py usa o cliente oficial huggingface_hub.InferenceClient, que escolhe
# automaticamente um Inference Provider compatível. A ordem é fixa para que
# uma variável de ambiente não altere silenciosamente o fallback solicitado:
#
#   1. warp-ai/wuerstchen ....................... sem provedor em 2026-08-16.
#   2. black-forest-labs/FLUX.1-schnell ......... disponível em vários.
#   3. stabilityai/stable-diffusion-xl-base-1.0 . disponível via fal-ai.
#   4. stable-diffusion-v1-5/stable-diffusion-v1-5
#      ........................................... sem provedor no momento.
#   5. prompthero/openjourney ................... sem provedor em 2026-08-16.
#
# Modelos sem provedor são pulados imediatamente; podem voltar a funcionar
# caso a Hugging Face passe a oferecê-los por algum Inference Provider.
HF_IMAGE_MODELS = (
    "warp-ai/wuerstchen",
    "black-forest-labs/FLUX.1-schnell",
    "stabilityai/stable-diffusion-xl-base-1.0",
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    "prompthero/openjourney",
)

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
