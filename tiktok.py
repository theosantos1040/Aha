"""Download de vídeos do TikTok via API pública tikwm.com (sem chave)."""
import requests

API = "https://www.tikwm.com/api/"


class TikTokError(Exception):
    pass


def fetch_video(url: str):
    """Retorna (bytes_do_video, titulo). Levanta TikTokError em falha."""
    url = url.strip()
    if "tiktok.com" not in url and "vm.tiktok" not in url:
        raise TikTokError("URL do TikTok inválida.")
    try:
        r = requests.get(API, params={"url": url, "hd": 1}, timeout=60)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        raise TikTokError(f"Erro de rede: {exc}")

    if data.get("code") != 0:
        raise TikTokError(data.get("msg", "Não foi possível baixar o vídeo."))

    info = data["data"]
    play_url = info.get("hdplay") or info.get("play")
    if not play_url:
        raise TikTokError("Vídeo sem link de download disponível.")
    if play_url.startswith("/"):
        play_url = "https://www.tikwm.com" + play_url

    try:
        vid = requests.get(play_url, timeout=120)
        vid.raise_for_status()
    except requests.RequestException as exc:
        raise TikTokError(f"Erro ao baixar o arquivo: {exc}")

    title = info.get("title") or "Vídeo do TikTok"
    return vid.content, title
