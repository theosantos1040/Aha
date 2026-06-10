"""Cliente de IA do ThzyxBoTS usando a API do OpenRouter.

Características:
- Suporta vários modelos gratuitos (testados com HTTP 200).
- Retenta automaticamente em 429/5xx/timeout com backoff exponencial.
- Faz fallback do campo `content` para `reasoning` (alguns modelos free
  só preenchem `reasoning`, ex.: z-ai/glm-4.5-air).
- Pode cair para outro modelo da lista se o escolhido continuar falhando.
"""
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


def _call_model(model_id: str, messages: list, timeout: int) -> str:
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/thzyxbots",
        "X-Title": config.BOT_NAME,
    }
    payload = {"model": model_id, "messages": messages, "max_tokens": 800}

    last_err = ""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                config.OPENROUTER_URL, headers=headers, json=payload, timeout=timeout
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
            text = _extract(data)
            if text:
                return text
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

        # erro não recuperável (ex.: 400, 401)
        raise AIError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:200]}")

    raise AIError(f"Falha após {MAX_RETRIES} tentativas ({last_err})")


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
