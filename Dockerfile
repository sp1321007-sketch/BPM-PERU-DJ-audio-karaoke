FROM python:3.10-slim

# Instalar FFmpeg y git
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar todas las librerías de IA y web directamente
RUN pip install --no-cache-dir gradio torch torchaudio demucs openai-whisper numpy

# Copiar el resto del proyecto
COPY . .

EXPOSE 7860

CMD ["python", "app.py"]
