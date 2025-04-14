# Étape 1 : Image de base optimisée
FROM python:3.11-slim

# Étape 2 : Installation de Tesseract et dépendances
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Étape 3 : Définir le répertoire de travail
WORKDIR /opt/render/project/src

# Étape 4 : Copier les fichiers du projet
COPY . .

# Étape 5 : Installer les dépendances Python
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Étape 6 : Vérification manuelle du path de Tesseract
RUN which tesseract || (echo "❌ Tesseract non trouvé dans le PATH !" && exit 1)

# Étape 7 : Définir la commande de démarrage
CMD ["python", "main.py"]
