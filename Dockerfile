FROM python:3.11-slim

# Installer tesseract + dépendances
RUN apt-get update && \
    apt-get install -y tesseract-ocr libglib2.0-0 libsm6 libxrender1 libxext6 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Créer dossier app et copier le code
WORKDIR /opt/render/project/src
COPY . .

# Installer les dépendances Python
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Commande pour lancer le bot
CMD ["python", "main.py"]
