import os
import logging
import shutil
import pytesseract
import subprocess
from PIL import Image
import io
from datetime import datetime
from fastapi import FastAPI
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes,
    filters
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import json

# Logging
logging.basicConfig(level=logging.INFO)

# Configuration des chemins Tesseract
os.environ["PATH"] = "/usr/bin:/usr/local/bin:/app/.apt/usr/bin:" + os.environ.get("PATH", "")
POTENTIAL_PATHS = ["/usr/bin/tesseract", "/usr/local/bin/tesseract", "/app/.apt/usr/bin/tesseract"]

# Vérification du binaire Tesseract
try:
    logging.info(f"🔍 PATH actuel : {os.environ.get('PATH')}")
    for path in ["/usr/bin", "/usr/local/bin", "/app/.apt/usr/bin"]:
        if os.path.exists(path):
            result = subprocess.run(["ls", "-la", path], capture_output=True, text=True)
            logging.info(f"📁 Contenu de {path} :\n{result.stdout}")
except Exception as e:
    logging.warning(f"Erreur lors de l'inspection du système : {e}")

try:
    version_check = subprocess.run(["tesseract", "-v"], capture_output=True, text=True)
    logging.info("📦 tesseract -v :")
    logging.info(version_check.stdout or version_check.stderr)
except Exception as e:
    logging.warning(f"❌ Erreur lors de l'exécution de tesseract -v : {e}")

which_result = shutil.which("tesseract")
logging.info(f"🔍 Résultat de shutil.which('tesseract') : {which_result}")

TESSERACT_PATH = which_result or next((p for p in POTENTIAL_PATHS if os.path.exists(p)), None)
if TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
    logging.info(f"✅ pytesseract utilisera : {TESSERACT_PATH}")
    try:
        version = pytesseract.get_tesseract_version()
        logging.info(f"📦 Version Tesseract (via pytesseract) : {version}")
        test_img = Image.new("RGB", (100, 30), color=(255, 255, 255))
        buf = io.BytesIO()
        test_img.save(buf, format='PNG')
        buf.seek(0)
        pytesseract.image_to_string(Image.open(buf))
        logging.info("🔍 Test OCR exécuté avec succès ✅")
    except Exception as e:
        logging.warning(f"⚠️ Impossible d'obtenir la version ou d'exécuter un test OCR : {e}")
else:
    logging.error("❌ Aucun chemin Tesseract trouvé. OCR désactivé.")

# 🔐 Google Sheets depuis variable JSON inline
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    raw_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not raw_json:
        raise ValueError("La variable GOOGLE_APPLICATION_CREDENTIALS_JSON est vide ou non définie")
    json_key = json.loads(raw_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(json_key, scope)
    sheet_client = gspread.authorize(creds)
    SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
    sheet = sheet_client.open_by_key(SPREADSHEET_ID)
    worksheet = sheet.worksheet("Suivi")
except Exception as e:
    worksheet = None
    logging.warning(f"❌ Erreur connexion Google Sheets : {e}")

# FastAPI
app = FastAPI()

# Telegram
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    logging.error("❌ TELEGRAM_BOT_TOKEN non trouvé dans les variables d'environnement !")
else:
    logging.info(f"✅ TELEGRAM_BOT_TOKEN détecté (longueur: {len(BOT_TOKEN)})")

# Pause courte pour Railway (propagation des env vars)
time.sleep(1)

WEBHOOK_URL = os.environ.get("RAILWAY_PUBLIC_URL")
PORT = int(os.environ.get("PORT", 8000))
application = Application.builder().token(BOT_TOKEN).build()

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot opérationnel ✅")

application.add_handler(CommandHandler("start", start))

# Image Handler
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        file_path = f"temp_{update.message.message_id}.jpg"
        await file.download_to_drive(file_path)

        text = pytesseract.image_to_string(Image.open(file_path))
        os.remove(file_path)

        await update.message.reply_text(f"🧠 OCR détecté :\n{text.strip()[:100]}...")

        if worksheet:
            now = datetime.now().strftime("%d/%m/%Y %H:%M")
            worksheet.append_row([now, update.effective_user.username, text.strip()[:500]])
            await update.message.reply_text("✅ Données ajoutées à Google Sheets")
        else:
            await update.message.reply_text("⚠️ Feuille Google Sheets non connectée")
    except Exception as e:
        logging.error(f"Erreur OCR : {e}")
        await update.message.reply_text("❌ Erreur lors du traitement de l'image")

application.add_handler(MessageHandler(filters.PHOTO, handle_image))

# Webhook FastAPI
if __name__ == "__main__":
    logging.info("✅ Démarrage du bot Telegram...")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL,
    )