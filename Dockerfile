FROM python:3.11-slim

# Installation de Tesseract et des langues nécessaires
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-fra \
    tesseract-ocr-eng \
    libgl1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Création du dossier de l'app
WORKDIR /app

# Copie des fichiers du projet dans le conteneur
COPY . .

# Installation des dépendances Python
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Lancement de l'application
CMD ["uvicorn", "main:app_fastapi", "--host", "0.0.0.0", "--port", "8000"]
