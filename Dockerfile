# Utilise une image slim avec python
FROM python:3.11-slim

# Empêche les prompts durant apt install
ENV DEBIAN_FRONTEND=noninteractive

# Installe tesseract et dépendances système
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libpoppler-cpp-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Crée le dossier pour l'app
WORKDIR /app

# Copie les fichiers
COPY . .

# Installe les dépendances Python
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Port d'écoute
ENV PORT=8000

# Lance le bot
CMD ["python", "main.py"]
