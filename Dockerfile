FROM python:3.11-slim

# 🔧 Installation Tesseract + dépendances nécessaires à Pillow / Tesseract
RUN apt-get update && \
    apt-get install -y tesseract-ocr libglib2.0-0 libsm6 libxrender1 libxext6 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 📁 Dossier de travail
WORKDIR /app

# 🧠 Copie du code
COPY . .

# 📦 Install dépendances Python
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# 🌍 Port pour FastAPI
ENV PORT=8000
EXPOSE 8000

# 🧪 TEST affichage emplacement + version de Tesseract dans les logs Render
CMD which tesseract && tesseract --version && python main.py
