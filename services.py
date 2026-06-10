"""Serviços externos simples: clima, tradução, QR, links, cripto, memes."""
import requests


def qr_png(text: str) -> bytes:
    """Gera um QR Code (PNG) via api.qrserver.com — sem chave."""
    r = requests.get(
        "https://api.qrserver.com/v1/create-qr-code/",
        params={"size": "400x400", "data": text}, timeout=30,
    )
    r.raise_for_status()
    return r.content


def shorten_url(url: str) -> str:
    """Encurta um link usando is.gd (sem chave)."""
    try:
        r = requests.get("https://is.gd/create.php",
                         params={"format": "simple", "url": url}, timeout=20)
        if r.status_code == 200 and r.text.startswith("http"):
            return r.text.strip()
        return f"❌ Não consegui encurtar: {r.text[:80]}"
    except Exception as exc:
        return f"❌ Erro ao encurtar: {exc}"


def crypto_price(coin: str) -> str:
    """Preço de uma criptomoeda via CoinGecko (sem chave)."""
    ids = {"btc": "bitcoin", "eth": "ethereum", "bnb": "binancecoin",
           "sol": "solana", "doge": "dogecoin", "ada": "cardano",
           "xrp": "ripple", "ltc": "litecoin"}
    cid = ids.get(coin.lower(), coin.lower())
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cid, "vs_currencies": "usd,brl",
                    "include_24hr_change": "true"}, timeout=25)
        r.raise_for_status()
        d = r.json().get(cid)
        if not d:
            return f"❌ Cripto '{coin}' não encontrada."
        chg = d.get("usd_24h_change", 0)
        emoji = "📈" if chg >= 0 else "📉"
        return (f"💰 *{cid.title()}*\n"
                f"💵 US$ {d['usd']:,.2f}\n"
                f"🇧🇷 R$ {d.get('brl', 0):,.2f}\n"
                f"{emoji} 24h: {chg:+.2f}%")
    except Exception as exc:
        return f"❌ Erro ao buscar cripto: {exc}"


def random_meme() -> tuple:
    """Retorna (url_imagem, titulo) de um meme via meme-api (sem chave)."""
    r = requests.get("https://meme-api.com/gimme", timeout=25)
    r.raise_for_status()
    d = r.json()
    return d.get("url"), d.get("title", "meme")


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
