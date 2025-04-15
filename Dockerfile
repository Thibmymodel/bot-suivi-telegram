# Étape 1 : image de base
FROM python:3.11-slim

# Étape 2 : installation des dépendances système (essentielles pour Tesseract et PIL)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
        poppler-utils \
        && rm -rf /var/lib/apt/lists/*

# Étape 3 : création du répertoire de l'app
WORKDIR /app

# Étape 4 : copie du code
COPY . .

# Étape 5 : installation des dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Étape 6 : expose le port utilisé par FastAPI
EXPOSE 8000

# Étape 7 : lance l'application
CMD ["python", "main.py"]
