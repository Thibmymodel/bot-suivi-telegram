import os
import re
import io
import json
import datetime
import logging
import shutil
from PIL import Image, ImageEnhance, ImageOps
import pytesseract
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Application, ContextTypes, MessageHandler, filters
import httpx

# --- CONFIG LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ENV VARIABLES ---
PORT = int(os.getenv("PORT", 8000))
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RAILWAY_URL = os.getenv("RAILWAY_PUBLIC_URL", "http://localhost:8000").rstrip("/")
GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
MODE_POLLING = os.getenv("MODE_POLLING", "false").lower() == "true"

# --- TELEGRAM APPLICATION ---
telegram_app = Application.builder().token(BOT_TOKEN).build()
bot = Bot(token=BOT_TOKEN)

# --- FASTAPI INITIALISATION ---
app = FastAPI()

# --- ROUTE POUR FORCER LE WEBHOOK À LA DEMANDE ---
@app.get("/force-webhook")
async def force_webhook():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                data={"url": f"{RAILWAY_URL}/webhook"}
            )
        logger.info(f"✅ Webhook forcé : {response.text}")
        return {"webhook_response": response.json()}
    except Exception as e:
        logger.error(f"❌ Erreur lors du reset webhook : {e}")
        return {"error": str(e)}

# --- SETUP TESSERACT ---
TESSERACT_PATH = shutil.which("tesseract")
pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH if TESSERACT_PATH else "tesseract"
logger.info(f"✅ Tesseract détecté : {pytesseract.pytesseract.tesseract_cmd}")

# --- SETUP GOOGLE SHEETS ---
credentials_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
creds_dict = json.loads(credentials_json)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Données Journalières")
logger.info("✅ Connexion Google Sheets réussie")

# --- UTILITY FUNCTIONS ---
def preprocess_image(image: Image.Image) -> Image.Image:
    image = image.convert("L")
    image = ImageOps.autocontrast(image)
    image = image.resize((image.width * 2, image.height * 2))
    return image

def extract_text_from_image(image_bytes: bytes) -> str:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        cropped = image.crop((0, 0, image.width, int(image.height * 0.4)))
        image = preprocess_image(cropped)
        text = pytesseract.image_to_string(image)
        logger.info(f"📄 OCR Result: {text}")
        return text
    except Exception as e:
        logger.error(f"Erreur OCR : {e}")
        return ""

def detect_network(text: str) -> str:
    t = text.lower()
    if "followers" in t and ("likes" in t or "following" in t):
        return "TikTok"
    elif "publications" in t and "followers" in t:
        return "Instagram"
    elif "threads" in t or "quoi de neuf" in t:
        return "Threads"
    elif ("abonnements" in t or "abonnés" in t) and "rejoint" in t:
        return "Twitter"
    return "Non détecté"

def extract_account_and_followers(text: str) -> tuple[str, int]:
    username_match = re.search(r"@[\w\.]+", text)
    account = username_match.group() if username_match else "Non détecté"

    number_match = re.findall(r"(\d+[.,\s]?\d*)\s*(k|followers|abonnés|k\s|k\n)", text.lower())
    if number_match:
        raw = number_match[0][0].replace(",", ".").replace(" ", "")
        try:
            number = float(raw)
            if "k" in number_match[0][1]:
                number *= 1000
            return account, int(number)
        except:
            pass

    return account, -1

def get_last_value_for_account(account: str, assistant: str) -> int:
    try:
        records = sheet.get_all_records()
        records = [r for r in records if r["Compte"] == account and r["Assistant"] == assistant]
        if not records:
            return 0
        last = sorted(records, key=lambda x: x["Date"], reverse=True)[0]
        return int(last["Abonnés"])
    except:
        return 0

def write_to_sheet(date: str, assistant: str, network: str, account: str, followers: int):
    previous = get_last_value_for_account(account, assistant)
    evolution = followers - previous if previous > 0 else ""
    sheet.append_row([date, assistant, network, account, followers, evolution])

# --- MAIN HANDLER ---
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = update.message
        if not message or not message.photo:
            return

        date_str = datetime.datetime.now().strftime("%Y-%m-%d")

        assistant_name = "general"
        if hasattr(message, "message_thread_id") and message.message_thread_id:
            topic_id = message.message_thread_id
            try:
                topic_info = await bot.get_forum_topic(chat_id=GROUP_ID, message_thread_id=topic_id)
                if topic_info.name.upper().startswith("SUIVI"):
                    assistant_name = topic_info.name.replace("SUIVI", "").strip().lower()
            except:
                pass

        photo = await message.photo[-1].get_file()
        image_bytes = await photo.download_as_bytearray()
        text = extract_text_from_image(image_bytes)

        accounts_data = []
        for match in re.finditer(r"@[\w\.]+", text):
            snippet = text[match.start():match.start()+200]
            account, followers = extract_account_and_followers(snippet)
            if followers == -1:
                continue
            network = detect_network(text)
            accounts_data.append((account, followers, network))
            write_to_sheet(date_str, assistant_name, network, account, followers)

        if accounts_data:
            msg = f"🤖 {date_str} – {assistant_name.upper()} – {len(accounts_data)} compte{'s' if len(accounts_data)>1 else ''} détecté{'s' if len(accounts_data)>1 else ''} et ajouté{'s' if len(accounts_data)>1 else ''} ✅"
        else:
            msg = f"❌ {date_str} – {assistant_name.upper()} – Analyse OCR impossible"

        await bot.send_message(chat_id=GROUP_ID, text=msg, message_thread_id=message.message_thread_id)

    except Exception as e:
        logger.exception("Erreur lors du traitement de l'image")
        fallback_msg = f"❌ {datetime.datetime.now().strftime('%Y-%m-%d')} – Analyse OCR impossible"
        await bot.send_message(chat_id=GROUP_ID, text=fallback_msg, message_thread_id=message.message_thread_id)

# --- DEBUG CATCH ALL MESSAGES ---
async def log_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"📥 Message reçu : {update.message}")
    logger.info(f"🧠 message_thread_id détecté : {getattr(update.message, 'message_thread_id', 'None')}")

# --- FASTAPI ROUTES ---
@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

@app.get("/")
def root():
    return {"status": "Bot operationnel"}

# --- REGISTER HANDLERS ---
telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_image))
telegram_app.add_handler(MessageHandler(filters.ALL, log_all_messages))

# --- MODE POLLING ---
if MODE_POLLING:
    import asyncio
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(telegram_app.run_polling())
else:
    logger.info(f"✅ Webhook Telegram activé : {RAILWAY_URL}/webhook")
    logger.info("ℹ️ Pour forcer le webhook manuellement : /force-webhook")