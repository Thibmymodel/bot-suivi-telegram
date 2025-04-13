import os
import io
import time
import json
import logging
import pytesseract
import datetime
import asyncio
import threading
from fastapi import FastAPI, Request
from PIL import Image
from telegram import Update, constants
from telegram.ext import (
    Application, ContextTypes, MessageHandler, filters, JobQueue
)
from google.oauth2.service_account import Credentials
import gspread

# Initialisation des logs
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

# =================== CONFIGURATION ===================

# Token Telegram (via Secret sur Render)
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Clé Google Sheets (chargée depuis /etc/secrets/credentials.json sur Render)
CREDENTIALS_PATH = "/etc/secrets/credentials.json"

# Nom de la feuille Google Sheets
SHEET_NAME = "Abonnés"
WORKSHEET_NAME = "Données"

# Delay OCR en secondes
OCR_DELAY_SECONDS = 180

# Regex Instagram simple (en fallback si besoin)
import re
USERNAME_REGEX = re.compile(r"@?([\w\.]+)")
FOLLOWERS_REGEX = re.compile(r"([\d.,]+)\s*abonnés", re.IGNORECASE)

# =================== GOOGLE SHEETS ===================

def init_gspread():
    credentials = Credentials.from_service_account_file(
        CREDENTIALS_PATH,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(credentials)
    sh = gc.open(SHEET_NAME)
    ws = sh.worksheet(WORKSHEET_NAME)
    return ws

worksheet = init_gspread()

# =================== OCR ADAPTATIF ===================

def try_ocr_variants(image_path: str) -> str:
    text = ""
    langs = ["eng+fra", "fra+eng", "eng", "fra"]
    for lang in langs:
        try:
            text = pytesseract.image_to_string(Image.open(image_path), lang=lang)
            if len(text.strip()) > 10:
                break
        except Exception as e:
            logging.warning(f"OCR failed with lang {lang}: {e}")
    return text.strip()

def extract_info_from_image(image_path: str):
    text = try_ocr_variants(image_path)
    logging.info(f"[OCR] Texte extrait :\n{text}")

    username_match = USERNAME_REGEX.search(text)
    followers_match = FOLLOWERS_REGEX.search(text)

    username = username_match.group(1) if username_match else "Inconnu"
    followers_raw = followers_match.group(1).replace(" ", "").replace(",", "").replace(".", "")

    try:
        followers = int(followers_raw)
    except ValueError:
        followers = -1

    return username, followers

# =================== TRAITEMENT DU SCREENSHOT ===================

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo:
        return

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    img_bytes = await file.download_as_bytearray()
    filename = f"temp_{int(time.time())}.jpg"

    with open(filename, "wb") as f:
        f.write(img_bytes)

    logging.info(f"[📸] Image téléchargée : {filename}")

    await asyncio.sleep(OCR_DELAY_SECONDS)

    try:
        username, followers = extract_info_from_image(filename)
        date = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        worksheet.append_row([username, followers, date])
        logging.info(f"[✅] Ajouté à Google Sheets : {username} – {followers}")

        await update.message.reply_text(
            f"✅ Analyse terminée :\n👤 {username}\n👥 {followers} abonnés"
        )

    except Exception as e:
        logging.error(f"[❌] Erreur OCR ou Google Sheets : {e}")
        await update.message.reply_text("❌ Une erreur est survenue lors de l’analyse.")

    finally:
        os.remove(filename)

# =================== FASTAPI + BOT INIT ===================

app = FastAPI()
bot_app = Application.builder().token(BOT_TOKEN).build()

@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    logging.info("🚀 Lancement local du serveur webhook sur http://localhost:8000")

    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_image))

    # Lancement dans thread séparé
    def launch_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot_app.initialize())
        loop.run_until_complete(bot_app.start())
        loop.run_until_complete(bot_app.updater.start_polling())  # nécessaire pour JobQueue
        loop.run_forever()

    threading.Thread(target=launch_bot, daemon=True).start()
