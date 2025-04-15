FROM python:3.11-slim

# Évite les questions d'apt
ENV DEBIAN_FRONTEND=noninteractive

# Installation de tesseract + dépendances
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Définir le chemin tesseract dans le PATH au cas où
ENV PATH="/usr/bin:${PATH}"

# Copie du code
WORKDIR /app
COPY . .

# Installation des dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Port utilisé par FastAPI
ENV PORT=8000

# Lancement
CMD ["python", "main.py"]
