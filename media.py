"""Conversão de mídia: imagem/vídeo -> figurinha (WebP) e vídeo -> áudio.

Estratégia robusta para Termux:
  * Figurinha de IMAGEM  -> feita com Pillow (NÃO precisa de ffmpeg).
  * Figurinha de VÍDEO   -> ffmpeg com encoder libwebp (com detecção e fallback).
  * Vídeo -> áudio       -> ffmpeg, tentando vários codecs de áudio.
  * Edição de IMAGEM     -> Pillow puro (girar/espelhar/pb/blur/pixelar/recortar).
  * Velocidade e reverso -> ffmpeg (áudio e vídeo).

Tudo aqui devolve bytes prontos; quem envia é o bot (send_sticker passthrough
/ send_audio). Usar passthrough=True evita o pipeline interno do neonize, que
depende de ffprobe/webpmux que costumam faltar no Termux.

ffmpeg no Termux:  pkg install ffmpeg -y
"""
import io
import math
import os
import shutil
import subprocess
import tempfile

# Limites dos parâmetros que chegam CRUS do WhatsApp. Sem eles um
# "/pixelar 999999" ou "/acelerar 0" derruba o processo do bot.
BLUR_MAX = 100.0          # raio do desfoque gaussiano
PIXEL_MIN, PIXEL_MAX = 2, 256      # tamanho do bloco da pixelização
FACTOR_MIN, FACTOR_MAX = 0.25, 4.0  # fator de velocidade
FFMPEG_TIMEOUT = 120      # segundos por chamada do ffmpeg
REVERSE_MAX_SECONDS = 60  # `reverse`/`areverse` carregam tudo na RAM


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


# ──────────────────────── ÁUDIO -> MP3 (p/ transcrição) ────────────────────────

def to_mp3(data: bytes) -> bytes:
    """Converte qualquer áudio para MP3.

    O áudio de voz do WhatsApp vem em OGG/Opus, mas a API de transcrição do
    OpenRouter só aceita `mp3` e `wav` — sem essa conversão o modelo recusa o
    arquivo.
    """
    if not has_ffmpeg():
        raise MediaError(
            "ffmpeg não encontrado — necessário para transcrever áudio. "
            "Instale com: pkg install ffmpeg -y (Termux)"
        )
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, "in.audio")
        out_path = os.path.join(tmp, "out.mp3")
        with open(in_path, "wb") as fh:
            fh.write(data)
        for extra in (["-acodec", "libmp3lame"], ["-c:a", "mp3"]):
            cmd = ["ffmpeg", "-y", "-i", in_path, "-vn", *extra, "-q:a", "4", out_path]
            proc = subprocess.run(cmd, capture_output=True, timeout=120)
            if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path):
                with open(out_path, "rb") as fh:
                    return fh.read()
        raise MediaError("não consegui converter esse áudio para MP3.")


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


# ──────────────────── EDIÇÃO DE IMAGEM (Pillow, sem ffmpeg) ────────────────────
# Tudo aqui roda com Pillow puro de propósito: o Termux quase nunca tem ffmpeg
# e figurinha estática é WebP, que o Pillow abre nativamente.

_IMAGE_KINDS = ("image", "sticker")


def _pil():
    """Importa o Pillow com uma mensagem de erro útil se faltar."""
    try:
        from PIL import Image, ImageFilter, ImageOps
    except ImportError as exc:
        raise MediaError("Pillow não instalado: pip install pillow") from exc
    return Image, ImageFilter, ImageOps


def _open_image(data: bytes, kind: str):
    """Valida o tipo de mídia e devolve a imagem já carregada (1º quadro)."""
    if kind not in _IMAGE_KINDS:
        raise MediaError(
            "essa operação só vale para imagem ou figurinha "
            f"(recebi '{kind or 'nada'}')."
        )
    if not data:
        raise MediaError("não recebi nenhuma imagem.")
    Image, _, _ = _pil()
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as exc:
        raise MediaError(f"imagem inválida: {exc}") from exc
    return im


def _has_alpha(im) -> bool:
    return im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info


def _encode_image(im) -> bytes:
    """Serializa a imagem: PNG quando há transparência, senão JPEG."""
    out = io.BytesIO()
    if _has_alpha(im):
        im.convert("RGBA").save(out, format="PNG", optimize=True)
    else:
        im.convert("RGB").save(out, format="JPEG", quality=90)
    return out.getvalue()


def sticker_to_image(data: bytes, kind: str = "sticker") -> bytes:
    """Figurinha (WebP) -> foto PNG (com transparência) ou JPEG. Só Pillow."""
    im = _open_image(data, kind)
    if _has_alpha(im):
        im = im.convert("RGBA")
    else:
        im = im.convert("RGB")
    return _encode_image(im)


def rotate_image(data: bytes, kind: str = "image", degrees: float = 90) -> bytes:
    """Gira a imagem no sentido horário. Aceita qualquer ângulo (normaliza % 360)."""
    Image, _, _ = _pil()
    try:
        degrees = float(degrees)
    except (TypeError, ValueError) as exc:
        raise MediaError("ângulo inválido: use um número, ex.: /girar 90") from exc
    if not math.isfinite(degrees):
        raise MediaError("ângulo inválido: use um número, ex.: /girar 90")
    degrees %= 360.0
    im = _open_image(data, kind)
    alpha = _has_alpha(im)
    im = im.convert("RGBA" if alpha else "RGB")
    if degrees:
        fill = (0, 0, 0, 0) if alpha else (255, 255, 255)
        # Pillow gira anti-horário; invertemos para bater com a expectativa.
        im = im.rotate(-degrees, resample=Image.BICUBIC, expand=True, fillcolor=fill)
    return _encode_image(im)


def mirror_image(data: bytes, kind: str = "image") -> bytes:
    """Espelha a imagem na horizontal (efeito selfie)."""
    _, _, ImageOps = _pil()
    im = _open_image(data, kind)
    alpha = _has_alpha(im)
    im = ImageOps.mirror(im.convert("RGBA" if alpha else "RGB"))
    return _encode_image(im)


def grayscale_image(data: bytes, kind: str = "image") -> bytes:
    """Deixa a imagem em preto e branco, preservando a transparência."""
    _, _, ImageOps = _pil()
    im = _open_image(data, kind)
    if _has_alpha(im):
        im = im.convert("RGBA")
        cinza = ImageOps.grayscale(im.convert("RGB")).convert("RGBA")
        cinza.putalpha(im.getchannel("A"))
        im = cinza
    else:
        im = ImageOps.grayscale(im.convert("RGB")).convert("RGB")
    return _encode_image(im)


def blur_image(data: bytes, kind: str = "image", radius: float = 5) -> bytes:
    """Desfoque gaussiano. `radius` de 0 a 100 (valores maiores travam o celular)."""
    _, ImageFilter, _ = _pil()
    try:
        radius = float(radius)
    except (TypeError, ValueError) as exc:
        raise MediaError(
            f"intensidade inválida: use um número de 0 a {BLUR_MAX:g}."
        ) from exc
    if not math.isfinite(radius) or not (0 <= radius <= BLUR_MAX):
        raise MediaError(
            f"intensidade fora do limite: use de 0 a {BLUR_MAX:g} (ex.: /blur 5)."
        )
    im = _open_image(data, kind)
    im = im.convert("RGBA" if _has_alpha(im) else "RGB")
    if radius:
        im = im.filter(ImageFilter.GaussianBlur(radius))
    return _encode_image(im)


def pixelate_image(data: bytes, kind: str = "image", size: int = 12) -> bytes:
    """Pixeliza a imagem. `size` é o tamanho do bloco, de 2 a 256."""
    Image, _, _ = _pil()
    try:
        size = int(float(size))
    except (TypeError, ValueError) as exc:
        raise MediaError(
            f"tamanho inválido: use um número inteiro de {PIXEL_MIN} a {PIXEL_MAX}."
        ) from exc
    if not (PIXEL_MIN <= size <= PIXEL_MAX):
        raise MediaError(
            f"tamanho fora do limite: use de {PIXEL_MIN} a {PIXEL_MAX} "
            "(ex.: /pixelar 12)."
        )
    im = _open_image(data, kind)
    im = im.convert("RGBA" if _has_alpha(im) else "RGB")
    largura, altura = im.size
    # reduz e amplia de volta com NEAREST -> blocos quadrados
    pequena = im.resize(
        (max(1, largura // size), max(1, altura // size)), Image.BILINEAR
    )
    im = pequena.resize((largura, altura), Image.NEAREST)
    return _encode_image(im)


def crop_square_image(data: bytes, kind: str = "image") -> bytes:
    """Recorta o centro da imagem num quadrado (lado = menor dimensão)."""
    im = _open_image(data, kind)
    im = im.convert("RGBA" if _has_alpha(im) else "RGB")
    largura, altura = im.size
    lado = min(largura, altura)
    esquerda = (largura - lado) // 2
    topo = (altura - lado) // 2
    im = im.crop((esquerda, topo, esquerda + lado, topo + lado))
    return _encode_image(im)


# ──────────────────── ÁUDIO/VÍDEO: VELOCIDADE E REVERSO ────────────────────

_AV_KINDS = ("audio", "video")


def _need_ffmpeg(para: str) -> None:
    """Levanta MediaError com instrução de instalação se faltar ffmpeg."""
    if not has_ffmpeg():
        raise MediaError(
            f"ffmpeg não encontrado — necessário para {para}. "
            "Instale com: pkg install ffmpeg -y (Termux) "
            "ou apt install ffmpeg -y (Linux)."
        )


def _check_av(data: bytes, kind: str) -> None:
    if kind not in _AV_KINDS:
        raise MediaError(
            "essa operação só vale para áudio ou vídeo "
            f"(recebi '{kind or 'nada'}')."
        )
    if not data:
        raise MediaError("não recebi nenhuma mídia.")


def _check_factor(factor) -> float:
    """Valida o fator de velocidade vindo do usuário (0.25 a 4.0)."""
    try:
        factor = float(factor)
    except (TypeError, ValueError) as exc:
        raise MediaError(
            f"fator inválido: use um número de {FACTOR_MIN:g} a {FACTOR_MAX:g}."
        ) from exc
    if not math.isfinite(factor) or not (FACTOR_MIN <= factor <= FACTOR_MAX):
        # Mensagem SEM citar um comando específico: esta função é usada tanto
        # por /acelerar quanto por /lentidao (speed_up_media/slow_down_media),
        # e citar sempre "/acelerar" aqui confundia quem tinha chamado /lentidao.
        raise MediaError(
            f"fator fora do limite: use um valor de {FACTOR_MIN:g} a {FACTOR_MAX:g}."
        )
    return factor


def _atempo_chain(tempo: float) -> str:
    """Monta a cadeia de `atempo` (cada etapa só aceita 0.5 a 2.0)."""
    partes = []
    restante = float(tempo)
    while restante > 2.0 + 1e-9:
        partes.append("atempo=2.0")
        restante /= 2.0
    while restante < 0.5 - 1e-9:
        partes.append("atempo=0.5")
        restante *= 2.0
    partes.append(f"atempo={restante:.6f}")
    return ",".join(partes)


def _write_tmp(tmp: str, data: bytes) -> str:
    caminho = os.path.join(tmp, "in.bin")
    with open(caminho, "wb") as fh:
        fh.write(data)
    return caminho


def _run_attempts(tmp: str, attempts, erro: str) -> bytes:
    """Roda cada tentativa de ffmpeg em ordem e devolve o 1º arquivo válido."""
    last_err = ""
    for args, outname in attempts:
        out_path = os.path.join(tmp, outname)
        proc = subprocess.run(
            ["ffmpeg", "-y", *args, out_path],
            capture_output=True, timeout=FFMPEG_TIMEOUT,
        )
        if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path):
            with open(out_path, "rb") as fh:
                return fh.read()
        last_err = proc.stderr.decode("utf-8", "ignore")[-200:]
    raise MediaError(f"{erro} {last_err}")


def _audio_attempts(in_path: str, afilter: str, limite=()):
    """Tentativas de saída de áudio (mp3 e, se faltar o encoder, aac)."""
    base = [*limite, "-i", in_path, "-vn", "-af", afilter]
    return [
        ([*base, "-acodec", "libmp3lame", "-q:a", "2"], "out.mp3"),
        ([*base, "-c:a", "mp3", "-q:a", "2"], "out.mp3"),
        ([*base, "-c:a", "aac", "-b:a", "192k"], "out.m4a"),
    ]


def _video_attempts(in_path: str, vfilter: str, afilter: str, limite=()):
    """Tentativas de saída de vídeo; cai para sem-áudio se o vídeo for mudo."""
    base = [*limite, "-i", in_path, "-vf", vfilter]
    x264 = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    return [
        ([*base, "-af", afilter, *x264, "-c:a", "aac", "-b:a", "128k"], "out.mp4"),
        ([*base, "-an", *x264], "out.mp4"),
        ([*base, "-an", "-c:v", "mpeg4", "-q:v", "5"], "out.mp4"),
    ]


def _apply_speed(data: bytes, kind: str, tempo: float) -> bytes:
    """Aplica um fator de tempo (>1 acelera, <1 desacelera) em áudio ou vídeo."""
    _check_av(data, kind)
    _need_ffmpeg("mudar a velocidade de áudio/vídeo")
    atempo = _atempo_chain(tempo)
    with tempfile.TemporaryDirectory() as tmp:
        in_path = _write_tmp(tmp, data)
        if kind == "audio":
            attempts = _audio_attempts(in_path, atempo)
        else:
            attempts = _video_attempts(in_path, f"setpts={1.0 / tempo:.6f}*PTS", atempo)
        return _run_attempts(tmp, attempts, "não consegui mudar a velocidade dessa mídia.")


def speed_up_media(data: bytes, kind: str = "audio", factor: float = 2) -> bytes:
    """Acelera áudio/vídeo em `factor` vezes (0.25 a 4.0). Precisa de ffmpeg."""
    return _apply_speed(data, kind, _check_factor(factor))


def slow_down_media(data: bytes, kind: str = "audio", factor: float = 2) -> bytes:
    """Deixa áudio/vídeo `factor` vezes mais lento (0.25 a 4.0). Precisa de ffmpeg."""
    return _apply_speed(data, kind, 1.0 / _check_factor(factor))


def reverse_media(data: bytes, kind: str = "audio") -> bytes:
    """Inverte áudio (areverse) ou vídeo (reverse + areverse). Precisa de ffmpeg.

    Os filtros de reverso carregam a mídia inteira na memória, então cortamos
    em REVERSE_MAX_SECONDS para não derrubar o celular.
    """
    _check_av(data, kind)
    _need_ffmpeg("inverter áudio/vídeo")
    limite = ["-t", str(REVERSE_MAX_SECONDS)]
    with tempfile.TemporaryDirectory() as tmp:
        in_path = _write_tmp(tmp, data)
        if kind == "audio":
            attempts = _audio_attempts(in_path, "areverse", limite)
        else:
            attempts = _video_attempts(in_path, "reverse", "areverse", limite)
        return _run_attempts(tmp, attempts, "não consegui inverter essa mídia.")


# ──────────────── FIGURINHA ANIMADA -> VÍDEO (precisa de ffmpeg) ────────────────

def animated_sticker_to_video(data: bytes, kind: str = "sticker") -> bytes:
    """Figurinha animada (WebP) -> MP4. Precisa de ffmpeg.

    Tenta o ffmpeg direto no WebP; se o build não souber demuxar WebP animado
    (comum no Termux), extrai os quadros com Pillow e monta o vídeo a partir deles.
    """
    if kind not in _IMAGE_KINDS:
        raise MediaError(
            f"envie uma figurinha animada (recebi '{kind or 'nada'}')."
        )
    Image, _, _ = _pil()
    im = _open_image(data, kind)
    quadros = int(getattr(im, "n_frames", 1) or 1)
    if quadros < 2:
        raise MediaError(
            "essa figurinha não é animada — use /toimg para virar foto."
        )
    _need_ffmpeg("converter figurinha animada em vídeo")

    duracao = int(im.info.get("duration") or 100)  # ms por quadro
    fps = max(1, min(30, round(1000 / max(20, duracao))))
    par = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    x264 = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]

    with tempfile.TemporaryDirectory() as tmp:
        webp_path = os.path.join(tmp, "in.webp")
        with open(webp_path, "wb") as fh:
            fh.write(data)
        out_path = os.path.join(tmp, "out.mp4")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", webp_path, "-vf", par, *x264, "-an", out_path],
            capture_output=True, timeout=FFMPEG_TIMEOUT,
        )
        if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path):
            with open(out_path, "rb") as fh:
                return fh.read()

        # Fallback: Pillow extrai os quadros e o ffmpeg só junta os PNGs.
        frames_dir = os.path.join(tmp, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        try:
            for indice in range(quadros):
                im.seek(indice)
                quadro = Image.new("RGB", im.size, (255, 255, 255))
                atual = im.convert("RGBA")
                quadro.paste(atual, (0, 0), atual)
                quadro.save(os.path.join(frames_dir, f"{indice:04d}.png"))
        except Exception as exc:
            raise MediaError(f"não consegui ler os quadros da figurinha: {exc}") from exc

        out2 = os.path.join(tmp, "out2.mp4")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-framerate", str(fps),
             "-i", os.path.join(frames_dir, "%04d.png"),
             "-vf", par, *x264, "-an", out2],
            capture_output=True, timeout=FFMPEG_TIMEOUT,
        )
        if proc.returncode != 0 or not os.path.exists(out2) or not os.path.getsize(out2):
            raise MediaError(
                "não consegui converter a figurinha animada em vídeo: "
                + proc.stderr.decode("utf-8", "ignore")[-200:]
            )
        with open(out2, "rb") as fh:
            return fh.read()
