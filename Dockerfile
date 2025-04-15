# Utilise une image officielle avec Python
FROM python:3.11-slim

# Empêche les invites interactives (utile pour apt-get)
ENV DEBIAN_FRONTEND=noninteractive

# Met à jour le système et installe tesseract + dépendances nécessaires
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    libleptonica-dev \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Définit le répertoire de travail
WORKDIR /app

# Copie tous les fichiers dans le conteneur
COPY . /app

# Installe les dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Ajoute explicitement le binaire tesseract au PATH (au cas où)
ENV PATH="/usr/bin:/usr/local/bin:$PATH"

# Définit la commande par défaut
CMD ["python", "main.py"]
