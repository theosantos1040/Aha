"""Conversão de mídia: vídeo -> áudio (ffmpeg) e figurinhas.

Figurinhas são tratadas pelo próprio neonize (send_sticker).
Para vídeo -> áudio é necessário o ffmpeg instalado:
    Termux:  pkg install ffmpeg -y
    Debian:  apt install ffmpeg -y
"""
import os
import shutil
import subprocess
import tempfile


class MediaError(Exception):
    pass


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def video_to_audio(video_bytes: bytes) -> bytes:
    """Extrai a trilha de áudio de um vídeo e devolve um MP3 em bytes."""
    if not has_ffmpeg():
        raise MediaError(
            "ffmpeg não encontrado. Instale com: pkg install ffmpeg -y (Termux)"
        )
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, "in.mp4")
        out_path = os.path.join(tmp, "out.mp3")
        with open(in_path, "wb") as fh:
            fh.write(video_bytes)
        cmd = [
            "ffmpeg", "-y", "-i", in_path,
            "-vn", "-acodec", "libmp3lame", "-q:a", "2", out_path,
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or not os.path.exists(out_path):
            raise MediaError(
                "Falha ao converter o vídeo. Ele tem áudio? "
                + proc.stderr.decode("utf-8", "ignore")[-200:]
            )
        with open(out_path, "rb") as fh:
            return fh.read()
