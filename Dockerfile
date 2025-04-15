FROM python:3.11-slim

# 🔧 Installation de Tesseract et bibliothèques nécessaires
RUN apt-get update && \
    apt-get install -y tesseract-ocr libglib2.0-0 libsm6 libxrender1 libxext6 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 📁 Répertoire de travail
WORKDIR /app

# 🧠 Copie du projet dans l'image
COPY . .

# 📦 Installation des dépendances Python
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# 🌍 Port exposé pour FastAPI
ENV PORT=8000
EXPOSE 8000

# ✅ 🔍 CMD debug ultra complet AVANT de lancer l'app
CMD echo "📌 PATH actuel : $PATH" && \
    echo "📌 Contenu de /usr/bin :" && ls -l /usr/bin | grep tesseract && \
    echo "📌 Emplacement de tesseract :" && which tesseract && \
    echo "📌 Version de tesseract :" && tesseract --version && \
    python main.py
