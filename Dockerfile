# Image de base avec apt complet pour Render
FROM python:3.11-slim

# Préparation des outils système
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    poppler-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Création du répertoire de travail
WORKDIR /app

# Copie du code
COPY . /app

# Installation des dépendances Python
RUN pip install --upgrade pip && pip install -r requirements.txt

# Définir le port pour Render
ENV PORT=10000
EXPOSE $PORT

# Commande de démarrage
CMD ["python", "main.py"]
