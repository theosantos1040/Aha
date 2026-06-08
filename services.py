"""Serviços externos simples: clima e tradução (sem chave de API)."""
import requests


def weather(city: str) -> str:
    try:
        r = requests.get(f"https://wttr.in/{city}", params={"format": "j1"}, timeout=30)
        r.raise_for_status()
        c = r.json()["current_condition"][0]
    except Exception as exc:
        return f"❌ Não consegui buscar o clima de '{city}'. ({exc})"
    desc = c["weatherDesc"][0]["value"]
    return (
        f"🌤️ *Clima em {city.title()}*\n"
        f"🌡️ Temperatura: {c['temp_C']}°C (sensação {c['FeelsLikeC']}°C)\n"
        f"📋 Condição: {desc}\n"
        f"💧 Umidade: {c['humidity']}%\n"
        f"💨 Vento: {c['windspeedKmph']} km/h"
    )


def translate(text: str, target: str = "en") -> str:
    # 1) tenta a API gratuita do Google
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": text},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        result = "".join(part[0] for part in data[0] if part[0])
        if result.strip():
            return result
    except Exception:
        pass
    # 2) fallback: traduz usando a IA do OpenRouter
    try:
        import ai
        return ai.chat(
            f"Traduza o texto a seguir para o idioma '{target}'. "
            f"Responda APENAS com a tradução, sem comentários:\n\n{text}"
        )
    except Exception as exc:
        return f"❌ Erro ao traduzir: {exc}"
