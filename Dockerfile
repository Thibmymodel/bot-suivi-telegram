# Utilise une image Debian avec Python
FROM python:3.11-slim

# Évite les prompts interactifs pendant l'installation
ENV DEBIAN_FRONTEND=noninteractive

# Installe Tesseract et dépendances essentielles
RUN apt-get update && \
    apt-get install -y tesseract-ocr libglib2.0-0 libsm6 libxrender1 libxext6 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Crée le dossier de l'application
WORKDIR /opt/app

# Copie les fichiers
COPY . .

# Installe les dépendances Python
RUN pip install --upgrade pip && pip install -r requirements.txt

# Définit la commande de lancement
CMD ["python", "main.py"]
