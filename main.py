import os
import logging
import shutil
import pytesseract
import subprocess
from PIL import Image, ImageEnhance, ImageFilter
import io
from datetime import datetime
from fastapi import FastAPI
from telegram import Update, Message
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

try:
    version_check = subprocess.run(["tesseract", "-v"], capture_output=True, text=True)
    logging.info("\ud83d\udcc6 tesseract -v :")
    logging.info(version_check.stdout or version_check.stderr)
except Exception as e:
    logging.warning(f"❌ Erreur lors de l'exécution de tesseract -v : {e}")

which_result = shutil.which("tesseract")
logging.info(f"🔍 Résultat de shutil.which('tesseract') : {which_result}")

TESSERACT_PATH = which_result or next((p for p in POTENTIAL_PATHS if os.path.exists(p)), None)
if TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
    logging.info(f"✅ pytesseract utilisera : {TESSERACT_PATH}")
else:
    logging.error("❌ Aucun chemin Tesseract trouvé. OCR désactivé.")

# Connexion Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    raw_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not raw_json or not raw_json.strip().startswith("{"):
        raise ValueError("La variable GOOGLE_APPLICATION_CREDENTIALS_JSON est vide ou invalide")
    json_key = json.loads(raw_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(json_key, scope)
    sheet_client = gspread.authorize(creds)
    SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
    sheet = sheet_client.open_by_key(SPREADSHEET_ID)
    worksheet = sheet.worksheet("Données Journalières")
except Exception as e:
    worksheet = None
    logging.warning(f"❌ Erreur connexion Google Sheets : {e}")

# FastAPI
app = FastAPI()

# Telegram
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("RAILWAY_PUBLIC_URL")
PORT = int(os.environ.get("PORT", 8000))
GENERAL_TOPIC_NAME = "Général"
GROUP_ID = int(os.environ.get("TELEGRAM_GROUP_ID", "0"))
application = Application.builder().token(BOT_TOKEN).build()

def preprocess_image(img_path):
    image = Image.open(img_path).convert("L").filter(ImageFilter.SHARPEN)
    enhancer = ImageEnhance.Contrast(image)
    return enhancer.enhance(2)

async def post_to_general(context, message: str):
    try:
        await context.bot.send_message(chat_id=GROUP_ID, text=message, message_thread_id=await get_general_topic_id(context))
    except Exception as e:
        logging.error(f"❌ Erreur envoi message dans Général : {e}")

async def get_general_topic_id(context) -> int:
    try:
        topics = await context.bot.get_forum_topic_list(GROUP_ID)
        for topic in topics.topics:
            if GENERAL_TOPIC_NAME.lower() in topic.name.lower():
                return topic.message_thread_id
    except Exception as e:
        logging.error(f"❌ Erreur récupération topic Général : {e}")
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot opérationnel ✅")

application.add_handler(CommandHandler("start", start))

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        file_path = f"temp_{update.message.message_id}.jpg"
        await file.download_to_drive(file_path)

        image = preprocess_image(file_path)
        text = pytesseract.image_to_string(image, config='--psm 6')
        os.remove(file_path)

        if not text.strip():
            await update.message.reply_text("❌ Aucun texte OCR détecté")
            await post_to_general(context, f"❌ {datetime.now().strftime('%d/%m')} – Aucune donnée exploitable détectée")
            return

        assistant = f"@{update.effective_user.username}"
        reseau, compte = "Non détecté", "Non détecté"

        if "followers" in text.lower() and "publications" in text.lower():
            reseau = "Instagram"
        elif "followers" in text.lower() and "j'aime" in text.lower():
            reseau = "TikTok"
        elif "threads" in text.lower():
            reseau = "Threads"
        elif "tweets" in text.lower() or "abonnements" in text.lower():
            reseau = "Twitter"

        for line in text.splitlines():
            if line.strip().startswith("@"):  # identifiant du compte
                compte = line.strip().split()[0]
                break

        abonnes = next((int(w) for w in text.replace(",", "").split() if w.isdigit() and 100 < int(w) < 10_000_000), "?")

        evolution = "?"
        mots = text.replace("+", " ").replace("-", " ").split()
        for i in range(len(mots) - 1):
            if mots[i].lower().startswith("j-1") and mots[i+1].isdigit():
                evolution = int(mots[i+1])
                break

        now = datetime.now().strftime("%Y-%m-%d 00:00:00")

        if worksheet:
            worksheet.append_row([now, assistant, reseau, compte, abonnes, evolution])
            await update.message.reply_text("✅ Données ajoutées à Google Sheets")
            await post_to_general(context, f"🤖 {datetime.now().strftime('%d/%m')} – 1 compte détecté et ajouté ✅")
        else:
            await update.message.reply_text("⚠️ Feuille Google Sheets non connectée")
            await post_to_general(context, f"❌ {datetime.now().strftime('%d/%m')} – Feuille Google Sheet non connectée")

    except Exception as e:
        logging.error(f"Erreur OCR : {e}")
        await update.message.reply_text("❌ Erreur lors du traitement de l'image")
        await post_to_general(context, f"❌ {datetime.now().strftime('%d/%m')} – Erreur lors de l'analyse de l'image")

application.add_handler(MessageHandler(filters.PHOTO, handle_image))

if __name__ == "__main__":
    logging.info("✅ Démarrage du bot Telegram...")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL,
    )
