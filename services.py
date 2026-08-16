"""Serviços externos simples: clima, tradução, QR, links, cripto, memes."""
import re
import urllib.parse

import requests


def qr_png(text: str) -> bytes:
    """Gera um QR Code (PNG) LOCALMENTE, sem rede.

    Antes isso ia para api.qrserver.com. Duas razões para não fazer mais isso:

    1. Segurança: o QR de pareamento do WhatsApp carrega as credenciais de
       vinculação do aparelho. Mandar esse conteúdo para um servidor de
       terceiros entrega a quem o receber a chance de parear no seu lugar.
    2. Confiabilidade: era uma chamada HTTP bloqueante (timeout de 30s) feita
       DENTRO do callback de QR do whatsmeow, que roda a cada ~20s. Se o
       serviço demorasse ou estivesse bloqueado (rede do Render, por exemplo),
       o QR simplesmente nunca aparecia.

    O `segno` já vem junto com o neonize, então não há dependência nova.
    """
    import io

    import segno

    buf = io.BytesIO()
    segno.make_qr(text).save(buf, kind="png", scale=10, border=2)
    return buf.getvalue()


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


# ===================== CONSULTAS (CEP, DDD, MOEDA, WIKI, DICIONÁRIO) =====================

def cep(consulta: str) -> str:
    """Consulta um endereço pelo CEP usando o ViaCEP (sem chave).

    Aceita o CEP com ou sem hífen/ponto ("01001-000", "01001000").
    """
    digitos = re.sub(r"\D", "", consulta or "")
    if len(digitos) != 8:
        return ("❌ CEP inválido. Envie os 8 dígitos.\n"
                "Exemplo: `/cep 01001-000`")
    try:
        r = requests.get(f"https://viacep.com.br/ws/{urllib.parse.quote(digitos)}/json/",
                         timeout=20)
        r.raise_for_status()
        d = r.json()
    except Exception as exc:
        return f"❌ Erro ao consultar o CEP: {exc}"
    if not isinstance(d, dict) or d.get("erro"):
        return f"❌ CEP {digitos[:5]}-{digitos[5:]} não encontrado."

    rua = d.get("logradouro") or "—"
    if d.get("complemento"):
        rua = f"{rua} ({d['complemento']})"
    linhas = [f"📍 *CEP {d.get('cep') or digitos}*",
              f"🏠 Logradouro: {rua}",
              f"🏘️ Bairro: {d.get('bairro') or '—'}",
              f"🏙️ Cidade: {d.get('localidade') or '—'} - {d.get('uf') or '—'}"]
    if d.get("regiao"):
        linhas.append(f"🗺️ Região: {d['regiao']}")
    if d.get("ddd"):
        linhas.append(f"📞 DDD: {d['ddd']}")
    return "\n".join(linhas)


def ddd(consulta: str) -> str:
    """Estado e cidades de um DDD usando a BrasilAPI (sem chave)."""
    digitos = re.sub(r"\D", "", consulta or "")
    if len(digitos) != 2:
        return ("❌ DDD inválido. Envie apenas os 2 dígitos.\n"
                "Exemplo: `/ddd 11`")
    try:
        r = requests.get(f"https://brasilapi.com.br/api/ddd/v1/{urllib.parse.quote(digitos)}",
                         timeout=20)
        if r.status_code in (400, 404):
            return f"❌ DDD {digitos} não existe no Brasil."
        r.raise_for_status()
        d = r.json()
    except Exception as exc:
        return f"❌ Erro ao consultar o DDD: {exc}"

    estado = (d.get("state") or "—") if isinstance(d, dict) else "—"
    cidades = d.get("cities") or [] if isinstance(d, dict) else []
    linhas = [f"📞 *DDD {digitos}*", f"🗺️ Estado: {estado}"]
    if cidades:
        amostra = sorted(c.title() for c in cidades if c)[:10]
        linhas.append(f"🏙️ Cidades atendidas: {len(cidades)}")
        linhas.append("📍 Algumas delas: " + ", ".join(amostra))
        if len(cidades) > len(amostra):
            linhas[-1] += "..."
    return "\n".join(linhas)


def _moeda_awesomeapi(base: str, cotada: str):
    """Cotação via AwesomeAPI. Devolve a string pronta ou None se falhar."""
    try:
        r = requests.get(
            f"https://economia.awesomeapi.com.br/json/last/{base}-{cotada}", timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
        if not isinstance(d, dict) or not d:
            return None
        info = d.get(f"{base}{cotada}") or next(iter(d.values()))
        preco = float(info["bid"])
    except Exception:
        return None
    linhas = [f"💱 *{info.get('name') or f'{base}/{cotada}'}*",
              f"💰 1 {base} = {preco:,.4f} {cotada}"]
    try:
        variacao = float(info.get("pctChange", 0))
        linhas.append(f"{'📈' if variacao >= 0 else '📉'} Variação: {variacao:+.2f}%")
    except Exception:
        pass
    try:
        linhas.append(f"🔼 Máx: {float(info['high']):,.4f}  🔽 Mín: {float(info['low']):,.4f}")
    except Exception:
        pass
    if info.get("create_date"):
        linhas.append(f"🕒 Atualizado: {info['create_date']}")
    return "\n".join(linhas)


def _moeda_open_er(base: str, cotada: str):
    """Plano B: open.er-api.com (sem chave). Devolve a string pronta ou None."""
    try:
        r = requests.get(f"https://open.er-api.com/v6/latest/{base}", timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
        if not isinstance(d, dict) or d.get("result") != "success":
            return None
        preco = d.get("rates", {}).get(cotada)
        if preco is None:
            return None
        preco = float(preco)
    except Exception:
        return None
    linhas = [f"💱 *{base}/{cotada}*", f"💰 1 {base} = {preco:,.4f} {cotada}"]
    if d.get("time_last_update_utc"):
        linhas.append(f"🕒 Atualizado: {d['time_last_update_utc']}")
    return "\n".join(linhas)


def moeda(par: str) -> str:
    """Cotação de um par de moedas, ex.: "USD-BRL" (também aceita "usd brl")."""
    bruto = (par or "").strip()
    match = re.fullmatch(r"([A-Za-z]{3})\s*[-/ ]?\s*([A-Za-z]{3})", bruto)
    if not match:
        return ("❌ Par de moedas inválido. Use o formato XXX-YYY.\n"
                "Exemplos: `/moeda USD-BRL`, `/moeda EUR-BRL`")
    base, cotada = match.group(1).upper(), match.group(2).upper()
    if base == cotada:
        return "❌ Escolha duas moedas diferentes. Exemplo: `/moeda USD-BRL`"

    texto = _moeda_awesomeapi(base, cotada) or _moeda_open_er(base, cotada)
    if texto:
        return texto
    return (f"❌ Não consegui a cotação de {base}-{cotada} agora "
            "(moeda inexistente ou serviço fora do ar). Tente novamente.")


def wiki(termo: str) -> str:
    """Resumo de um assunto na Wikipédia em português (sem chave)."""
    busca = " ".join((termo or "").split())
    if not busca:
        return "❌ Diga o que você quer pesquisar. Exemplo: `/wiki Brasil`"
    if len(busca) > 200:
        return "❌ Termo muito longo para pesquisar na Wikipédia."

    alvo = urllib.parse.quote(busca.replace(" ", "_"), safe="")
    try:
        r = requests.get(
            f"https://pt.wikipedia.org/api/rest_v1/page/summary/{alvo}",
            headers={"User-Agent": "AhaBot/1.0 (WhatsApp bot)"}, timeout=20)
        if r.status_code == 404:
            return f"❌ Não encontrei nada sobre '{busca}' na Wikipédia."
        r.raise_for_status()
        d = r.json()
    except Exception as exc:
        return f"❌ Erro ao consultar a Wikipédia: {exc}"

    resumo = (d.get("extract") or "").strip()
    if d.get("type") == "disambiguation":
        return (f"🤔 '{busca}' pode significar várias coisas na Wikipédia. "
                "Tente ser mais específico.")
    if not resumo:
        return f"❌ Não encontrei um resumo sobre '{busca}' na Wikipédia."
    if len(resumo) > 900:
        resumo = resumo[:900].rstrip() + "..."

    linhas = [f"📚 *{d.get('title') or busca}*"]
    if d.get("description"):
        linhas.append(f"_{d['description']}_")
    linhas.append("")
    linhas.append(resumo)
    link = (d.get("content_urls") or {}).get("desktop", {}).get("page")
    if link:
        linhas.append(f"\n🔗 {link}")
    return "\n".join(linhas)


def _definicoes_do_xml(xml: str) -> list:
    """Extrai os significados do XML do Dicionário Aberto."""
    significados = []
    try:
        import xml.etree.ElementTree as ET
        raiz = ET.fromstring(xml)
        for sense in raiz.iter("sense"):
            gram = (sense.findtext("gramGrp") or "").strip()
            for bloco in sense.iter("def"):
                texto = "".join(bloco.itertext())
                for linha in texto.splitlines():
                    linha = " ".join(linha.split()).replace("_", "")
                    if linha:
                        significados.append(f"({gram}) {linha}" if gram else linha)
                        gram = ""
    except Exception:
        # Plano B: tira as tags na marra
        texto = re.sub(r"<[^>]+>", "\n", xml or "")
        significados = [" ".join(l.split()).replace("_", "")
                        for l in texto.splitlines() if l.strip()]
    return significados


def dicionario(palavra: str) -> str:
    """Significado de uma palavra pelo Dicionário Aberto (sem chave)."""
    termo = (palavra or "").strip()
    if not termo:
        return "❌ Diga a palavra que você quer consultar. Exemplo: `/dicionario casa`"
    if not re.fullmatch(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", termo) or len(termo) > 40:
        return "❌ Envie uma única palavra, só com letras. Exemplo: `/dicionario casa`"

    try:
        r = requests.get(
            f"https://api.dicionario-aberto.net/word/{urllib.parse.quote(termo.lower(), safe='')}",
            timeout=20)
        if r.status_code == 404:
            return f"❌ Não encontrei '{termo}' no dicionário."
        r.raise_for_status()
        d = r.json()
    except Exception as exc:
        return f"❌ Erro ao consultar o dicionário: {exc}"

    if not isinstance(d, list) or not d:
        return f"❌ Não encontrei '{termo}' no dicionário."

    significados = []
    for entrada in d:
        if isinstance(entrada, dict):
            significados.extend(_definicoes_do_xml(entrada.get("xml") or ""))
        if len(significados) >= 8:
            break
    if not significados:
        return f"❌ Não encontrei o significado de '{termo}'."

    linhas = [f"📖 *{termo.capitalize()}*", ""]
    for i, sig in enumerate(significados[:8], 1):
        if len(sig) > 220:
            sig = sig[:220].rstrip() + "..."
        linhas.append(f"{i}. {sig}")
    linhas.append("\n_Fonte: Dicionário Aberto_")
    return "\n".join(linhas)


# Apelidos em português para os fusos mais pedidos (o resto vem do zoneinfo).
_FUSOS = {
    "brasilia": "America/Sao_Paulo", "brasil": "America/Sao_Paulo",
    "sao paulo": "America/Sao_Paulo", "rio de janeiro": "America/Sao_Paulo",
    "manaus": "America/Manaus", "belem": "America/Belem",
    "fortaleza": "America/Fortaleza", "recife": "America/Recife",
    "salvador": "America/Bahia", "cuiaba": "America/Cuiaba",
    "campo grande": "America/Campo_Grande", "porto velho": "America/Porto_Velho",
    "rio branco": "America/Rio_Branco", "acre": "America/Rio_Branco",
    "fernando de noronha": "America/Noronha", "noronha": "America/Noronha",
    "utc": "UTC", "gmt": "UTC",
    "portugal": "Europe/Lisbon", "lisboa": "Europe/Lisbon",
    "londres": "Europe/London", "inglaterra": "Europe/London",
    "paris": "Europe/Paris", "franca": "Europe/Paris",
    "madri": "Europe/Madrid", "madrid": "Europe/Madrid", "espanha": "Europe/Madrid",
    "berlim": "Europe/Berlin", "alemanha": "Europe/Berlin",
    "roma": "Europe/Rome", "italia": "Europe/Rome",
    "moscou": "Europe/Moscow", "russia": "Europe/Moscow",
    "nova york": "America/New_York", "new york": "America/New_York",
    "eua": "America/New_York", "estados unidos": "America/New_York",
    "los angeles": "America/Los_Angeles", "california": "America/Los_Angeles",
    "chicago": "America/Chicago", "miami": "America/New_York",
    "cidade do mexico": "America/Mexico_City", "mexico": "America/Mexico_City",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "argentina": "America/Argentina/Buenos_Aires",
    "santiago": "America/Santiago", "chile": "America/Santiago",
    "lima": "America/Lima", "peru": "America/Lima",
    "bogota": "America/Bogota", "colombia": "America/Bogota",
    "toquio": "Asia/Tokyo", "tokyo": "Asia/Tokyo", "japao": "Asia/Tokyo",
    "pequim": "Asia/Shanghai", "china": "Asia/Shanghai",
    "xangai": "Asia/Shanghai", "hong kong": "Asia/Hong_Kong",
    "seul": "Asia/Seoul", "coreia": "Asia/Seoul",
    "india": "Asia/Kolkata", "nova delhi": "Asia/Kolkata",
    "dubai": "Asia/Dubai", "emirados": "Asia/Dubai",
    "israel": "Asia/Jerusalem", "jerusalem": "Asia/Jerusalem",
    "sydney": "Australia/Sydney", "australia": "Australia/Sydney",
    "angola": "Africa/Luanda", "luanda": "Africa/Luanda",
    "mocambique": "Africa/Maputo", "maputo": "Africa/Maputo",
    "africa do sul": "Africa/Johannesburg", "cabo verde": "Atlantic/Cape_Verde",
}

_DIAS_SEMANA = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
                "sexta-feira", "sábado", "domingo")


def _sem_acento(texto: str) -> str:
    import unicodedata
    normal = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in normal if not unicodedata.combining(c))


def _achar_fuso(local: str):
    """Descobre o fuso IANA a partir do que o usuário digitou. None se não achar."""
    from zoneinfo import available_timezones

    chave = _sem_acento(" ".join(local.split())).casefold()
    if chave in _FUSOS:
        return _FUSOS[chave]

    zonas = available_timezones()
    # Nome IANA completo (America/Sao_Paulo), sem ligar para maiúsculas
    procurado = chave.replace(" ", "_")
    for zona in zonas:
        if zona.casefold() == procurado:
            return zona
    # Só a cidade final do nome IANA (ex.: "tokyo" -> Asia/Tokyo)
    for zona in sorted(zonas):
        if zona.rsplit("/", 1)[-1].casefold().replace("_", " ") == chave:
            return zona
    return None


def horario(local: str) -> str:
    """Hora atual em outro fuso horário. Não usa rede (zoneinfo da stdlib)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    pedido = " ".join((local or "").split())
    if not pedido:
        return ("❌ Diga a cidade ou o fuso.\n"
                "Exemplos: `/horario Tóquio`, `/horario Londres`, "
                "`/horario America/Sao_Paulo`")

    zona = _achar_fuso(pedido)
    if not zona:
        return (f"❌ Não conheço o fuso de '{pedido}'.\n"
                "Tente uma cidade grande (`/horario Londres`) ou o nome IANA "
                "(`/horario America/Sao_Paulo`).")
    try:
        agora = datetime.now(ZoneInfo(zona))
    except Exception as exc:
        return f"❌ Erro ao calcular o horário: {exc}"

    deslocamento = agora.utcoffset()
    total = int(deslocamento.total_seconds()) if deslocamento else 0
    sinal = "+" if total >= 0 else "-"
    utc = f"UTC{sinal}{abs(total) // 3600:02d}:{(abs(total) % 3600) // 60:02d}"
    # Se o usuário já digitou o nome IANA, não repete no cabeçalho.
    if _sem_acento(pedido).casefold().replace(" ", "_") == zona.casefold():
        cabecalho = f"🕒 *{zona}*"
    else:
        cabecalho = f"🕒 *{pedido.title()}* ({zona})"
    return (f"{cabecalho}\n"
            f"⏰ {agora.strftime('%H:%M:%S')}\n"
            f"📅 {_DIAS_SEMANA[agora.weekday()]}, {agora.strftime('%d/%m/%Y')}\n"
            f"🌐 {utc}")
