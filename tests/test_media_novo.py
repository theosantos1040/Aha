"""Testes das novas operações de mídia (imagem com Pillow, áudio/vídeo com ffmpeg).

As funções de imagem NÃO dependem de ffmpeg e são sempre testadas.
As de velocidade/reverso são puladas automaticamente quando falta ffmpeg.
"""
import io
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import media  # noqa: E402
from PIL import Image  # noqa: E402

TEM_FFMPEG = shutil.which("ffmpeg") is not None
sem_ffmpeg = pytest.mark.skipif(not TEM_FFMPEG, reason="ffmpeg não instalado")


# ─────────────────────────── auxiliares ───────────────────────────

def png_bytes(largura=120, altura=80, cor=(200, 30, 30)) -> bytes:
    """Gera um PNG real com um retângulo colorido no canto (assimétrico)."""
    im = Image.new("RGB", (largura, altura), cor)
    for x in range(largura // 4):
        for y in range(altura // 4):
            im.putpixel((x, y), (10, 220, 40))
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def webp_sticker(largura=100, altura=60) -> bytes:
    """Gera uma figurinha estática WebP com transparência."""
    im = Image.new("RGBA", (largura, altura), (0, 0, 255, 255))
    im.putpixel((0, 0), (0, 0, 0, 0))
    out = io.BytesIO()
    im.save(out, format="WEBP")
    return out.getvalue()


def webp_animado(quadros=6, lado=64) -> bytes:
    """Gera uma figurinha animada WebP."""
    imagens = [
        Image.new("RGBA", (lado, lado), (i * 30 % 256, 100, 200, 255))
        for i in range(quadros)
    ]
    out = io.BytesIO()
    imagens[0].save(
        out, format="WEBP", save_all=True, append_images=imagens[1:],
        duration=100, loop=0,
    )
    return out.getvalue()


def abrir(payload: bytes):
    """Abre os bytes devolvidos e garante que são uma imagem válida."""
    assert isinstance(payload, bytes) and payload, "retorno vazio"
    im = Image.open(io.BytesIO(payload))
    im.load()
    return im


def _ffmpeg_gera(args, saida, tmp_path):
    caminho = os.path.join(str(tmp_path), saida)
    proc = subprocess.run(
        ["ffmpeg", "-y", *args, caminho], capture_output=True, timeout=120
    )
    if proc.returncode != 0 or not os.path.exists(caminho):
        pytest.skip("ffmpeg local não conseguiu gerar a mídia de teste")
    with open(caminho, "rb") as fh:
        return fh.read()


@pytest.fixture
def audio_teste(tmp_path):
    return _ffmpeg_gera(
        ["-f", "lavfi", "-i", "sine=frequency=440:duration=2"], "a.mp3", tmp_path
    )


@pytest.fixture
def video_teste(tmp_path):
    return _ffmpeg_gera(
        ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
        "v.mp4", tmp_path,
    )


# ─────────────────────────── sticker_to_image ───────────────────────────

def test_sticker_to_image_mantem_dimensoes():
    saida = media.sticker_to_image(webp_sticker(100, 60), "sticker")
    im = abrir(saida)
    assert im.size == (100, 60)
    assert im.format == "PNG"  # tem transparência -> PNG


def test_sticker_to_image_sem_alpha_vira_jpeg():
    saida = media.sticker_to_image(png_bytes(50, 40), "image")
    im = abrir(saida)
    assert im.size == (50, 40)
    assert im.format == "JPEG"


def test_sticker_to_image_recusa_kind_invalido():
    with pytest.raises(media.MediaError):
        media.sticker_to_image(png_bytes(), "audio")


def test_imagem_invalida_levanta_erro():
    with pytest.raises(media.MediaError):
        media.sticker_to_image(b"isso nao e uma imagem", "image")


# ─────────────────────────── rotate_image ───────────────────────────

def test_rotate_90_troca_largura_e_altura():
    im = abrir(media.rotate_image(png_bytes(120, 80), "image", 90))
    assert im.size == (80, 120)


def test_rotate_180_mantem_dimensoes():
    im = abrir(media.rotate_image(png_bytes(120, 80), "image", 180))
    assert im.size == (120, 80)


def test_rotate_normaliza_com_modulo_360():
    a = abrir(media.rotate_image(png_bytes(120, 80), "image", 450))
    b = abrir(media.rotate_image(png_bytes(120, 80), "image", -270))
    assert a.size == b.size == (80, 120)


def test_rotate_360_equivale_a_zero():
    im = abrir(media.rotate_image(png_bytes(120, 80), "image", 360))
    assert im.size == (120, 80)


def test_rotate_preserva_transparencia():
    im = abrir(media.rotate_image(webp_sticker(100, 60), "sticker", 90))
    assert im.size == (60, 100)
    assert im.mode == "RGBA"


@pytest.mark.parametrize("valor", ["abc", float("nan"), float("inf"), None])
def test_rotate_angulo_invalido(valor):
    with pytest.raises(media.MediaError):
        media.rotate_image(png_bytes(), "image", valor)


# ─────────────────────────── mirror / grayscale ───────────────────────────

def test_mirror_mantem_dimensoes_e_espelha():
    original = png_bytes(120, 80)
    im = abrir(media.mirror_image(original, "image"))
    assert im.size == (120, 80)
    # o quadrado verde estava no canto esquerdo; agora deve estar no direito
    assert im.convert("RGB").getpixel((119, 0))[1] > 150


def test_mirror_recusa_kind_invalido():
    with pytest.raises(media.MediaError):
        media.mirror_image(png_bytes(), "video")


def test_grayscale_deixa_canais_iguais():
    im = abrir(media.grayscale_image(png_bytes(60, 40), "image")).convert("RGB")
    assert im.size == (60, 40)
    r, g, b = im.getpixel((59, 39))
    assert abs(r - g) <= 2 and abs(g - b) <= 2


def test_grayscale_preserva_alpha_da_figurinha():
    im = abrir(media.grayscale_image(webp_sticker(40, 30), "sticker"))
    assert im.mode == "RGBA"
    assert im.getpixel((0, 0))[3] == 0


# ─────────────────────────── blur ───────────────────────────

def test_blur_valido_mantem_dimensoes():
    im = abrir(media.blur_image(png_bytes(120, 80), "image", 5))
    assert im.size == (120, 80)


def test_blur_zero_e_permitido():
    im = abrir(media.blur_image(png_bytes(30, 30), "image", 0))
    assert im.size == (30, 30)


@pytest.mark.parametrize("radius", [-1, 100.5, 101, 999999, "muito", None])
def test_blur_fora_do_limite(radius):
    with pytest.raises(media.MediaError):
        media.blur_image(png_bytes(), "image", radius)


def test_blur_limite_maximo_aceito():
    im = abrir(media.blur_image(png_bytes(40, 40), "image", media.BLUR_MAX))
    assert im.size == (40, 40)


# ─────────────────────────── pixelate ───────────────────────────

def test_pixelate_mantem_dimensoes_e_altera_pixels():
    original = png_bytes(120, 80)
    saida = media.pixelate_image(original, "image", 12)
    im = abrir(saida)
    assert im.size == (120, 80)
    assert saida != original


@pytest.mark.parametrize("size", [0, 1, -5, 257, 999999, "grande", None])
def test_pixelate_fora_do_limite(size):
    with pytest.raises(media.MediaError):
        media.pixelate_image(png_bytes(), "image", size)


@pytest.mark.parametrize("size", [2, 256])
def test_pixelate_limites_aceitos(size):
    im = abrir(media.pixelate_image(png_bytes(120, 80), "image", size))
    assert im.size == (120, 80)


# ─────────────────────────── crop_square ───────────────────────────

@pytest.mark.parametrize("largura,altura,lado", [(120, 80, 80), (60, 200, 60), (50, 50, 50)])
def test_crop_square_devolve_quadrado(largura, altura, lado):
    im = abrir(media.crop_square_image(png_bytes(largura, altura), "image"))
    assert im.size == (lado, lado)
    assert im.width == im.height


def test_crop_square_em_figurinha():
    im = abrir(media.crop_square_image(webp_sticker(100, 60), "sticker"))
    assert im.size == (60, 60)


def test_crop_square_recusa_kind_invalido():
    with pytest.raises(media.MediaError):
        media.crop_square_image(png_bytes(), "audio")


# ─────────────────── validação de fator (não precisa de ffmpeg) ───────────────────

@pytest.mark.parametrize("factor", [0, 0.24, -2, 4.1, 999999, "rapido", None,
                                    float("nan"), float("inf")])
def test_fator_fora_do_limite(factor):
    with pytest.raises(media.MediaError):
        media.speed_up_media(b"qualquer coisa", "audio", factor)
    with pytest.raises(media.MediaError):
        media.slow_down_media(b"qualquer coisa", "audio", factor)


def test_speed_recusa_kind_invalido():
    for func in (media.speed_up_media, media.slow_down_media):
        with pytest.raises(media.MediaError):
            func(png_bytes(), "image", 2)
    with pytest.raises(media.MediaError):
        media.reverse_media(png_bytes(), "image")


def test_atempo_encadeia_fora_de_0_5_a_2():
    assert media._atempo_chain(4.0).count("atempo") == 2
    assert media._atempo_chain(0.25).count("atempo") == 2
    assert media._atempo_chain(1.5).count("atempo") == 1
    for tempo in (0.25, 0.4, 0.5, 1.0, 2.0, 3.3, 4.0):
        for parte in media._atempo_chain(tempo).split(","):
            valor = float(parte.split("=")[1])
            assert 0.5 - 1e-6 <= valor <= 2.0 + 1e-6


@pytest.mark.skipif(TEM_FFMPEG, reason="ffmpeg está instalado")
def test_mensagem_de_erro_ensina_a_instalar_ffmpeg():
    with pytest.raises(media.MediaError) as exc:
        media.speed_up_media(b"dados", "audio", 2)
    assert "pkg install ffmpeg" in str(exc.value)


# ─────────────────────────── ffmpeg: áudio e vídeo ───────────────────────────

@sem_ffmpeg
def test_acelerar_audio(audio_teste):
    saida = media.speed_up_media(audio_teste, "audio", 2)
    assert isinstance(saida, bytes) and len(saida) > 100


@sem_ffmpeg
def test_desacelerar_audio(audio_teste):
    saida = media.slow_down_media(audio_teste, "audio", 2)
    assert isinstance(saida, bytes) and len(saida) > 100


@sem_ffmpeg
def test_acelerar_audio_fator_extremo(audio_teste):
    rapido = media.speed_up_media(audio_teste, "audio", 4)
    lento = media.slow_down_media(audio_teste, "audio", 4)
    assert len(rapido) > 100 and len(lento) > 100
    assert len(rapido) < len(lento)


@sem_ffmpeg
def test_reverter_audio(audio_teste):
    saida = media.reverse_media(audio_teste, "audio")
    assert isinstance(saida, bytes) and len(saida) > 100


@sem_ffmpeg
def test_acelerar_video(video_teste):
    saida = media.speed_up_media(video_teste, "video", 2)
    assert isinstance(saida, bytes) and len(saida) > 100


@sem_ffmpeg
def test_reverter_video(video_teste):
    saida = media.reverse_media(video_teste, "video")
    assert isinstance(saida, bytes) and len(saida) > 100


@sem_ffmpeg
def test_figurinha_animada_para_video():
    saida = media.animated_sticker_to_video(webp_animado(), "sticker")
    assert isinstance(saida, bytes) and len(saida) > 100


@sem_ffmpeg
def test_figurinha_animada_usa_fallback_de_quadros(monkeypatch):
    """Se o ffmpeg não souber ler WebP animado (Termux), o Pillow extrai os quadros."""
    original = subprocess.run

    def falso_run(cmd, *args, **kwargs):
        if any(str(parte).endswith("in.webp") for parte in cmd):
            return subprocess.CompletedProcess(cmd, 1, b"", b"webp demuxer ausente")
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(media.subprocess, "run", falso_run)
    saida = media.animated_sticker_to_video(webp_animado(), "sticker")
    assert isinstance(saida, bytes) and len(saida) > 100


def test_figurinha_estatica_nao_vira_video():
    with pytest.raises(media.MediaError) as exc:
        media.animated_sticker_to_video(webp_sticker(), "sticker")
    assert "toimg" in str(exc.value)


def test_animated_sticker_recusa_kind_invalido():
    with pytest.raises(media.MediaError):
        media.animated_sticker_to_video(webp_animado(), "video")
