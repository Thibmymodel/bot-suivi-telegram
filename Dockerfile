FROM python:3.11-slim

# Dépendances système pour Tesseract + librairies utiles
RUN apt-get update && \
    apt-get install -y tesseract-ocr libglib2.0-0 libsm6 libxext6 libxrender-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Création d'un répertoire de travail
WORKDIR /app

# Copie des fichiers
COPY . .

# Installation des dépendances Python
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Lancement de FastAPI avec uvicorn
CMD ["uvicorn", "main:app_fastapi", "--host", "0.0.0.0", "--port", "10000"]
