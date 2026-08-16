# ThzyxBoTS — imagem para deploy (Render, Railway, qualquer host com Docker)
FROM python:3.11-slim

# Dependências de sistema:
#  - ffmpeg/ffprobe -> /ttkvd, /fg (vídeo), /va
#  - libmagic1 + file -> o neonize precisa para iniciar
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libmagic1 \
        file \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# instala as dependências primeiro (melhor cache de build)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# código
COPY . .

# pasta do disco persistente (sessão do WhatsApp + banco do bot ficam aqui)
RUN mkdir -p /data
ENV SESSION_DB=/data/session.sqlite3 \
    DATA_DB=/data/bot_data.sqlite3 \
    PYTHONUNBUFFERED=1

# porta da página de pareamento (QR + código) quando LOGIN_METHOD=web
# o Render define $PORT sozinho; localmente usa 8080 por padrão
EXPOSE 8080

CMD ["python", "run.py"]
