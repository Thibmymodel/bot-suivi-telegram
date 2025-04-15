# Utilise une image Python officielle
FROM python:3.11-slim

# Empêche les invites interactives de bloquer l'installation
ENV DEBIAN_FRONTEND=noninteractive

# Installe les dépendances système et Tesseract
RUN apt-get update && \
    apt-get install -y tesseract-ocr libtesseract-dev libleptonica-dev gcc && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Crée le dossier de travail
WORKDIR /app

# Copie les fichiers dans le conteneur
COPY . /app

# Installe les dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Port par défaut pour FastAPI
ENV PORT=8000

# Lance le script principal
CMD ["python", "main.py"]
