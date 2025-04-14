FROM debian:bullseye-slim

# Installation de Python et Tesseract
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    tesseract-ocr \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Définir le dossier de travail
WORKDIR /app

# Copier les fichiers du projet
COPY . .

# Installer les dépendances Python
RUN pip3 install --no-cache-dir -r requirements.txt

# Port utilisé par FastAPI
EXPOSE 10000

# Démarrer l'app
CMD ["uvicorn", "main:app_fastapi", "--host", "0.0.0.0", "--port", "10000"]
