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

# GERAÇÃO DE IMAGEM (/gerarimagem) — ATENÇÃO: o OpenRouter não tem nenhum
# modelo gratuito que gere imagem, e black-forest-labs/flux.2-klein-4b não
# existe no catálogo deles. Este é o mais barato que realmente gera imagem;
# exige créditos na conta. Troque via AI_IMAGE_MODEL se quiser outro.
AI_IMAGE_MODEL = os.getenv("AI_IMAGE_MODEL", "google/gemini-2.5-flash-image")

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
