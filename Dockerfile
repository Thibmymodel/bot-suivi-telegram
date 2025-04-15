FROM python:3.11-slim

# 🧰 Installation de Tesseract OCR et ses dépendances
RUN apt-get update && \
    apt-get install -y tesseract-ocr libglib2.0-0 libsm6 libxrender1 libxext6 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 📁 Dossier de travail
WORKDIR /app

# 📦 Copie du projet dans le conteneur
COPY . .

# 🐍 Installation des dépendances Python
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# ✅ Test manuel possible : which tesseract ou tesseract --version
# (tu pourras faire ça dans Render → Shell)

# 🌐 Port pour FastAPI
ENV PORT=8000
EXPOSE 8000

# 🚀 Commande de lancement du bot
CMD ["python", "main.py"]
