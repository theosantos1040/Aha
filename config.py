"""Configuração central do ThzyxBoTS."""
import os


def _load_env_manual():
    """Lê o arquivo .env sem depender do pacote python-dotenv.

    No Termux é comum o python-dotenv não instalar; este fallback garante
    que a chave da IA seja carregada mesmo assim.
    """
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                # não sobrescreve variáveis já definidas no ambiente
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


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

# Modelos de IA disponíveis no comando /IA.
# Foram testados de verdade contra o OpenRouter (HTTP 200).
# Chave amigável -> id real do modelo.
AI_MODELS = {
    "chatgpt": "openai/gpt-oss-120b:free",   # rápido e estável (padrão)
    "nex": "nex-agi/nex-n2-pro:free",         # existe, porém mais lento
    "glm": "z-ai/glm-4.5-air:free",           # devolve texto em "reasoning"
}
DEFAULT_AI_MODEL = "chatgpt"

# Recompensas / economia
DAILY_REWARD = 250
START_BALANCE = 100

# XP por mensagem
XP_PER_MESSAGE = 10

# Símbolos decorativos do bot 🌸
DECO_TOP = "𓊆ྀི❤︎𓊇 ◡̈"
DECO_LINE = "•︡ᯅ•︠ ────────── •︡ᯅ•︠"
DECO_NAME = f"𓊆ྀི {BOT_NAME} ❤︎𓊇 ◡̈"
