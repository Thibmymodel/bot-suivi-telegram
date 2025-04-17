import os
import io
import re
import json
import shutil
import logging
import datetime
from difflib import get_close_matches
from PIL import Image, ImageOps
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

# --- CHARGEMENT DES HANDLES ---
try:
    with open("known_handles.json", "r", encoding="utf-8") as f:
        KNOWN_HANDLES = json.load(f)
    logger.info("📂 known_handles.json chargé avec succès")
except Exception as e:
    KNOWN_HANDLES = {}
    logger.warning(f"⚠️ Échec chargement known_handles.json : {e}")

def corriger_username(text: str, reseau: str) -> str:
    handles = KNOWN_HANDLES.get(reseau.lower(), [])
    for handle in handles:
        if f"@{handle}".lower() in text.lower():
            logger.info(f"🔎 Handle exact trouvé dans OCR : @{handle}")
            return handle
    # fallback sur approche approximative
    usernames = re.findall(r"@([a-zA-Z0-9_.]{3,})", text)
    for u in usernames:
        candidats = get_close_matches(u.lower(), handles, n=1, cutoff=0.85)
        if candidats:
            logger.info(f"🔁 Correction OCR : '{u}' → '{candidats[0]}'")
            return candidats[0]
    return "Non trouvé"

# --- PHOTO HANDLER ---
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
        photo = message.photo[-1]

        file = await bot.get_file(photo.file_id)
        img_bytes = await file.download_as_bytearray()
        image = Image.open(io.BytesIO(img_bytes))
        width, height = image.size
        cropped = image.crop((0, 0, width, int(height * 0.4)))
        enhanced = ImageOps.autocontrast(cropped)

        text = pytesseract.image_to_string(enhanced)
        logger.info(f"🔍 OCR brut :\n{text}")

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

        username = corriger_username(text, reseau)
        if username == "Non trouvé":
            raise ValueError("Nom d'utilisateur non trouvé avec OCR")

        logger.info(f"🕵️ Username final : '{username}' (réseau : {reseau})")

        abonnés = None
        text_clean = text.replace("\n", " ")

        triplet_regex = re.compile(r"(\d{1,3}(?:[ .,]\d{3})?)\s+(\d{1,3}(?:[ .,]\d{3})?)\s+(\d{1,3}(?:[ .,]\d{3})?)")
        triplet_match = triplet_regex.search(text_clean)
        if triplet_match:
            candidates = [triplet_match.group(i).replace(" ", "").replace(",", "").replace(".", "") for i in range(1, 4)]
            label_match = re.search(r"publications\s+followers\s+suivi\(e\)s", text_clean, re.IGNORECASE)
            if label_match:
                abonnés = candidates[1]

        if not abonnés:
            pattern_stats = re.compile(r"(\d{1,3}(?:[ .,]\d{3})*)(?=\s*(followers|abonn[ée]s?|j'aime|likes))", re.IGNORECASE)
            match = pattern_stats.search(text_clean)
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

        await bot.send_message(
            chat_id=GROUP_ID,
            text=f"🤬 {today} - {assistant} - 1 compte détecté et ajouté ✅"
        )

    except Exception as e:
        logger.exception("❌ Erreur traitement handle_photo")
        await bot.send_message(chat_id=GROUP_ID, text=f"❌ {datetime.datetime.now().strftime('%d/%m')} - Analyse OCR impossible")

# --- FASTAPI + LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    def runner():
        async def start():
            try:
                logger.info("🚦 Initialisation LIFESPAN → Telegram bot")
                await telegram_app.initialize()
                telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
                asyncio.create_task(telegram_app.start())
                telegram_ready.set()
                async with httpx.AsyncClient() as client:
                    res = await client.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                        data={"url": f"{RAILWAY_URL}/webhook"}
                    )
                    logger.info(f"🔗 Webhook enregistré → {res.status_code} | {res.text}")
            except Exception as e:
                logger.exception("❌ Échec init Telegram")
        asyncio.run(start())
    threading.Thread(target=runner, daemon=True).start()
    yield

app = FastAPI(lifespan=lifespan)
logger.info("🚀 FastAPI instance déclarée (avec lifespan)")

@app.get("/")
async def root():
    logger.info("📱 Ping reçu sur /")
    return {"status": "Bot opérationnel"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    logger.info("📨 Webhook reçu → traitement en cours...")
    try:
        await telegram_ready.wait()
        update_data = await request.json()
        update = Update.de_json(update_data, bot)
        await telegram_app.process_update(update)
        return JSONResponse(content={"ok": True})
    except Exception as e:
        logger.exception("❌ Erreur traitement /webhook")
        return JSONResponse(status_code=500, content={"error": str(e)})
