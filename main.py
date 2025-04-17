import os
import io
import re
import json
import shutil
import logging
import datetime
from difflib import get_close_matches
from PIL import Image, ImageOps, ImageEnhance, ImageFilter
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from telegram import Update, Bot, Message
from telegram.ext import Application, ContextTypes, MessageHandler, filters
import pytesseract
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import httpx
import asyncio
import threading

# --- LOGS ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ENV ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RAILWAY_URL = os.getenv("RAILWAY_PUBLIC_URL", "http://localhost:8000").rstrip("/")
GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
logger.info(f"🔑 BOT_TOKEN: {'PRÉSENT' if BOT_TOKEN else 'ABSENT'}")
logger.info(f"🔑 RAILWAY_URL: {RAILWAY_URL}")
logger.info(f"🔑 GROUP_ID: {GROUP_ID}")
logger.info(f"🔑 SPREADSHEET_ID: {SPREADSHEET_ID}")

# --- TELEGRAM ---
telegram_app = Application.builder().token(BOT_TOKEN).build()
bot = telegram_app.bot
telegram_ready = asyncio.Event()

# --- TESSERACT ---
pytesseract.pytesseract.tesseract_cmd = shutil.which("tesseract") or "tesseract"
logger.info(f"✅ Tesseract détecté : {pytesseract.pytesseract.tesseract_cmd}")

# --- GOOGLE SHEET ---
creds_dict = json.loads(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Données Journalières")
logger.info("✅ Connexion Google Sheets réussie")

# --- DOUBLONS ---
already_processed = set()
message_counter = {}

# --- CHARGEMENT DES HANDLES ---
try:
    with open("known_handles.json", "r", encoding="utf-8") as f:
        KNOWN_HANDLES = json.load(f)
    logger.info("📂 known_handles.json chargé avec succès")
except Exception as e:
    KNOWN_HANDLES = {}
    logger.warning(f"⚠️ Échec chargement known_handles.json : {e}")

def corriger_username(username_ocr: str, reseau: str) -> str:
    handles = KNOWN_HANDLES.get(reseau.lower(), [])
    username_ocr_clean = username_ocr.strip().encode("utf-8", "ignore").decode()
    candidats = get_close_matches(username_ocr_clean.lower(), handles, n=1, cutoff=0.6)
    if candidats:
        logger.info(f"🔁 Correction OCR : '{username_ocr}' → '{candidats[0]}'")
        return candidats[0]
    return username_ocr

# --- HANDLER PHOTO ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = update.message
        if not message or not message.photo:
            return

        thread_id = message.message_thread_id
        reply = message.reply_to_message
        if not reply or not hasattr(reply, "forum_topic_created"):
            return

        topic_name = reply.forum_topic_created.name
        if not topic_name.startswith("SUIVI "):
            return

        assistant = topic_name.replace("SUIVI ", "").strip().upper()
        message_counter.setdefault((datetime.datetime.now().strftime("%d/%m/%Y"), assistant), 0)

        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        img_bytes = await file.download_as_bytearray()
        image = Image.open(io.BytesIO(img_bytes))
        width, height = image.size
        cropped = image.crop((0, 0, width, int(height * 0.4)))
        enhanced = ImageOps.autocontrast(cropped)

        text = pytesseract.image_to_string(enhanced)
        logger.info(f"🔍 OCR brut :\n{text}")

        # --- Détection réseau social ---
        if "getallmylinks.com" in text.lower():
            reseau = "instagram"
        elif "beacons.ai" in text.lower():
            reseau = "twitter"
        elif "tiktok" in text.lower() or any(k in text.lower() for k in ["followers", "j'aime"]):
            reseau = "tiktok"
        elif "threads" in text.lower():
            reseau = "threads"
        elif any(x in text.lower() for x in ["modifier le profil", "suivi(e)s", "publications"]):
            reseau = "instagram"
        else:
            reseau = "instagram"

        usernames = re.findall(r"@([a-zA-Z0-9_.]{3,})", text)
        reseau_handles = KNOWN_HANDLES.get(reseau.lower(), [])
        username = "Non trouvé"
        for u in usernames:
            matches = get_close_matches(u.lower(), reseau_handles, n=1, cutoff=0.6)
            if matches:
                username = matches[0]
                break
        if username == "Non trouvé" and usernames:
            username = usernames[0]

        if username == "Non trouvé":
            urls = re.findall(r"getallmylinks\.com/([a-zA-Z0-9_.]+)", text)
            for u in urls:
                match = get_close_matches(u.lower(), reseau_handles, n=1, cutoff=0.6)
                if match:
                    username = match[0]
                    break
                username = u

        username = corriger_username(username, reseau)
        logger.info(f"🕵️ Username final : '{username}' (réseau : {reseau})")

        abonnés = None
        if reseau == "instagram":
            pattern_three_numbers = re.compile(r"(\d{1,3}(?:[ .,]\d{3})?)\s+(\d{1,3}(?:[ .,]\d{3})?)\s+(\d{1,3}(?:[ .,]\d{3})?)")
            match = pattern_three_numbers.search(text.replace("\n", " "))
            if match:
                abonnés = match.group(2).replace(" ", "").replace(".", "").replace(",", "")

        if not abonnés:
            pattern_stats = re.compile(r"(\d{1,3}(?:[ .,]\d{3})*)(?=\s*(followers|abonn[\u00e9e]s?|j'aime|likes))", re.IGNORECASE)
            match = pattern_stats.search(text.replace("\n", " "))
            if match:
                abonnés = match.group(1).replace(" ", "").replace(".", "").replace(",", "")

        if not username or not abonnés:
            raise ValueError("Nom d'utilisateur ou abonnés introuvable dans l'OCR")

        if message.message_id in already_processed:
            logger.info("⚠️ Message déjà traité, on ignore.")
            return
        already_processed.add(message.message_id)

        today = datetime.datetime.now().strftime("%d/%m/%Y")
        row = [today, assistant, reseau, f"@{username}", abonnés, ""]
        sheet.append_row(row)

        message_counter[(today, assistant)] += 1

    except Exception as e:
        logger.exception("❌ Erreur traitement handle_photo")
        await bot.send_message(chat_id=GROUP_ID, text=f"❌ {datetime.datetime.now().strftime('%d/%m')} - Analyse OCR impossible")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_app.initialize()
    telegram_app.create_task(telegram_app.start())
    telegram_ready.set()
    yield
    await telegram_app.stop()

app = FastAPI(lifespan=lifespan)

@telegram_app.message_handler(filters.PHOTO)
async def photo_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_photo(update, context)

@telegram_app.on_stop
async def stop_bot():
    logger.info("⏹️ Arrêt du bot Telegram")

@telegram_app.on_start
async def start_bot(_: Application):
    logger.info("🚀 Bot Telegram lancé en tâche de fond")

@telegram_app.post_init
async def post_init(_: Application):
    await bot.set_webhook(url=f"{RAILWAY_URL}/webhook")
    logger.info(f"🔗 Webhook enregistré → {RAILWAY_URL}/webhook")

@app.post("/webhook")
async def telegram_webhook(req: Request):
    body = await req.body()
    logger.info(f"📥 Webhook reçu → traitement en cours...")
    await telegram_ready.wait()
    await telegram_app.update_queue.put(Update.de_json(json.loads(body), bot))
    return JSONResponse(content={"status": "ok"})
