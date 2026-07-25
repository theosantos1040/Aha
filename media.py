"""Conversão de mídia: imagem/vídeo -> figurinha (WebP) e vídeo -> áudio.

Estratégia robusta para Termux:
  * Figurinha de IMAGEM  -> feita com Pillow (NÃO precisa de ffmpeg).
  * Figurinha de VÍDEO   -> ffmpeg com encoder libwebp (com detecção e fallback).
  * Vídeo -> áudio       -> ffmpeg, tentando vários codecs de áudio.

Tudo aqui devolve bytes prontos; quem envia é o bot (send_sticker passthrough
/ send_audio). Usar passthrough=True evita o pipeline interno do neonize, que
depende de ffprobe/webpmux que costumam faltar no Termux.

ffmpeg no Termux:  pkg install ffmpeg -y
"""
import io
import os
import shutil
import subprocess
import tempfile


class MediaError(Exception):
    pass


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _ffmpeg_has_encoder(name: str) -> bool:
    """Confere se o ffmpeg instalado tem um encoder específico (ex.: libwebp)."""
    if not has_ffmpeg():
        return False
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=15,
        )
        return name in out.stdout
    except Exception:
        return False


# ─────────────────────────── FIGURINHAS ───────────────────────────

def image_to_sticker(image_bytes: bytes) -> bytes:
    """Converte qualquer imagem em um WebP 512x512 transparente (sem ffmpeg).

    Mantém a proporção e centraliza numa tela 512x512 — o formato que o
    WhatsApp espera para figurinhas. Usa apenas Pillow.
    """
    try:
        from PIL import Image
    except ImportError as exc:  # Pillow vem com o neonize, mas por garantia
        raise MediaError("Pillow não instalado: pip install pillow") from exc
    try:
        im = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    except Exception as exc:
        raise MediaError(f"imagem inválida: {exc}") from exc
    im.thumbnail((512, 512), Image.LANCZOS)
    canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    canvas.paste(im, ((512 - im.width) // 2, (512 - im.height) // 2), im)
    out = io.BytesIO()
    canvas.save(out, format="WEBP", quality=90, method=6)
    return out.getvalue()


def video_to_sticker(video_bytes: bytes) -> bytes:
    """Converte um vídeo curto em figurinha animada (WebP). Precisa de ffmpeg+libwebp."""
    if not has_ffmpeg():
        raise MediaError(
            "ffmpeg não encontrado para figurinha de vídeo. "
            "Instale: pkg install ffmpeg -y (Termux)"
        )
    if not _ffmpeg_has_encoder("libwebp"):
        raise MediaError(
            "seu ffmpeg não tem o encoder libwebp (comum no Termux). "
            "Figurinhas de vídeo não são possíveis; tente com uma imagem."
        )
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, "in.mp4")
        out_path = os.path.join(tmp, "out.webp")
        with open(in_path, "wb") as fh:
            fh.write(video_bytes)
        vf = (
            "scale='if(gt(iw,ih),512,-1)':'if(gt(iw,ih),-1,512)',"
            "fps=15,pad=512:512:-1:-1:color=white@0.0"
        )
        cmd = [
            "ffmpeg", "-y", "-t", "6", "-i", in_path,
            "-vcodec", "libwebp", "-vf", vf,
            "-loop", "0", "-preset", "default", "-an", "-vsync", "0",
            "-q:v", "60", out_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        if proc.returncode != 0 or not os.path.exists(out_path):
            raise MediaError(
                "falha ao gerar figurinha de vídeo: "
                + proc.stderr.decode("utf-8", "ignore")[-200:]
            )
        with open(out_path, "rb") as fh:
            return fh.read()


# ─────────────────────────── VÍDEO -> ÁUDIO ───────────────────────────

def video_to_audio(video_bytes: bytes) -> bytes:
    """Extrai a trilha de áudio de um vídeo e devolve um MP3 (com fallbacks)."""
    if not has_ffmpeg():
        raise MediaError(
            "ffmpeg não encontrado. Instale com: pkg install ffmpeg -y (Termux)"
        )
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, "in.mp4")
        with open(in_path, "wb") as fh:
            fh.write(video_bytes)
        # tenta codecs em ordem de preferência; alguns builds não têm libmp3lame
        attempts = [
            (["-vn", "-acodec", "libmp3lame", "-q:a", "2"], "out.mp3"),
            (["-vn", "-c:a", "mp3", "-q:a", "2"], "out.mp3"),
            (["-vn", "-c:a", "aac", "-b:a", "192k"], "out.m4a"),
        ]
        last_err = ""
        for extra, outname in attempts:
            out_path = os.path.join(tmp, outname)
            cmd = ["ffmpeg", "-y", "-i", in_path, *extra, out_path]
            proc = subprocess.run(cmd, capture_output=True, timeout=120)
            if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path):
                with open(out_path, "rb") as fh:
                    return fh.read()
            last_err = proc.stderr.decode("utf-8", "ignore")[-200:]
        raise MediaError(
            "não consegui extrair o áudio (o vídeo tem som?). " + last_err
        )


# ──────────────────────── VÍDEO -> FRAME (p/ visão) ────────────────────────

def video_frame(video_bytes: bytes) -> bytes:
    """Extrai um quadro do vídeo como PNG, para análise por IA de visão.

    Modelos de visão recebem imagem, não vídeo — então pegamos um frame
    representativo (1s de vídeo, ou o primeiro se for mais curto).
    """
    if not has_ffmpeg():
        raise MediaError(
            "ffmpeg não encontrado — necessário para analisar vídeo. "
            "Instale com: pkg install ffmpeg -y (Termux). "
            "Dica: enviar uma FOTO funciona sem ffmpeg."
        )
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, "in.mp4")
        out_path = os.path.join(tmp, "frame.png")
        with open(in_path, "wb") as fh:
            fh.write(video_bytes)
        # tenta pegar em 1s; se o vídeo for menor, cai para o primeiro frame
        for seek in (["-ss", "1"], []):
            cmd = ["ffmpeg", "-y", *seek, "-i", in_path,
                   "-frames:v", "1", "-vf", "scale=768:-1", out_path]
            proc = subprocess.run(cmd, capture_output=True, timeout=120)
            if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path):
                with open(out_path, "rb") as fh:
                    return fh.read()
        raise MediaError("não consegui extrair um quadro desse vídeo.")
