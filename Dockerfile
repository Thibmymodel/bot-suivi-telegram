FROM python:3.11-slim

# Empêche les invites interactives
ENV DEBIAN_FRONTEND=noninteractive

# Installation des dépendances système nécessaires
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    libleptonica-dev \
    gcc \
    build-essential \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Création du dossier de travail
WORKDIR /opt/render/project/src

# Copie des fichiers
COPY . .

# Installation des dépendances Python
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Exposition du port utilisé par FastAPI
ENV PORT 10000
EXPOSE 10000

# Commande de lancement
CMD ["python", "main.py"]
