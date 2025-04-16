import os
import io
import re
import json
import shutil
import logging
import datetime
from PIL import Image, ImageOps
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telegram import Update, Bot
from telegram.ext import Application, ContextTypes, MessageHandler, filters
import pytesseract
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import httpx
import asyncio
from contextlib import asynccontextmanager

# --- LOGS ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ENV ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RAILWAY_URL = os.getenv("RAILWAY_PUBLIC_URL", "http://localhost:8000").rstrip("/")
GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# --- TELEGRAM ---
telegram_app = Application.builder().token(BOT_TOKEN).build()
bot = telegram_app.bot
telegram_ready = asyncio.Event()

# --- FASTAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_app.initialize()
    await telegram_app.start()
    telegram_ready.set()
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            data={"url": f"{RAILWAY_URL}/webhook"}
        )
    logger.info(f"✅ Webhook Telegram activé : {RAILWAY_URL}/webhook")
    yield
    await telegram_app.stop()

app = FastAPI(lifespan=lifespan)

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

@app.get("/")
def root():
    return {"status": "Bot opérationnel"}

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

# --- UTILS ---
def preprocess_image(image: Image.Image) -> Image.Image:
    image = image.convert("L")
    image = ImageOps.autocontrast(image)
    image = image.resize((image.width * 2, image.height * 2))
    return image

def extract_text_from_image(image_bytes: bytes) -> str:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        cropped = image.crop((0, 0, image.width, int(image.height * 0.4)))
        processed = preprocess_image(cropped)
        text = pytesseract.image_to_string(processed)
        logger.info(f"📄 OCR : {text}")
        return text
    except Exception as e:
        logger.error(f"OCR Failed: {e}")
        return ""

def detect_network(text: str) -> str:
    t = text.lower()
    if "followers" in t and ("likes" in t or "following" in t): return "TikTok"
    if "publications" in t and "followers" in t: return "Instagram"
    if "threads" in t or "quoi de neuf" in t: return "Threads"
    if "abonnés" in t and "abonnements" in t: return "Twitter"
    return "Non détecté"

def extract_account_and_followers(text: str) -> tuple[str, int]:
    username = re.search(r"@\w+", text)
    account = username.group() if username else "Non détecté"
    numbers = re.findall(r"(\d+[.,\s]?\d*)\s*(k|followers|abonnés|k\s|k\n)", text.lower())
    if numbers:
        raw = numbers[0][0].replace(",", ".").replace(" ", "")
        try:
            val = float(raw)
            if "k" in numbers[0][1]: val *= 1000
            return account, int(val)
        except:
            pass
    return account, -1

def get_last_value_for_account(account: str, assistant: str) -> int:
    try:
        records = sheet.get_all_records()
        filt = [r for r in records if r["Compte"] == account and r["Assistant"] == assistant]
        if not filt:
            return 0
        last = sorted(filt, key=lambda x: x["Date"], reverse=True)[0]
        return int(last["Abonnés"])
    except:
        return 0

def write_to_sheet(date: str, assistant: str, network: str, account: str, followers: int):
    last = get_last_value_for_account(account, assistant)
    evo = followers - last if last > 0 else ""
    sheet.append_row([date, assistant, network, account, followers, evo])

# --- HANDLER ---
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info("📸 Image reçue")
        msg = update.message
        if not msg or not msg.photo:
            return

        date = datetime.datetime.now().strftime("%Y-%m-%d")
        assistant = "general"
        if msg.message_thread_id:
            try:
                topic = await bot.get_forum_topic(chat_id=GROUP_ID, message_thread_id=msg.message_thread_id)
                if topic.name.upper().startswith("SUIVI"):
                    assistant = topic.name.replace("SUIVI", "").strip().lower()
            except:
                pass

        photo = await msg.photo[-1].get_file()
        image_bytes = await photo.download_as_bytearray()
        text = extract_text_from_image(image_bytes)

        comptes = []
        for match in re.finditer(r"@\w+", text):
            snippet = text[match.start():match.start()+200]
            account, followers = extract_account_and_followers(snippet)
            if followers == -1:
                continue
            network = detect_network(text)
            comptes.append((account, followers, network))
            write_to_sheet(date, assistant, network, account, followers)

        msg_txt = f"🤖 {date} – {assistant.upper()} – {len(comptes)} compte(s) détecté(s) ✅" if comptes else f"❌ {date} – {assistant.upper()} – Analyse OCR impossible"
        await bot.send_message(chat_id=GROUP_ID, text=msg_txt, message_thread_id=msg.message_thread_id)
    except Exception as e:
        logger.exception("❌ ERREUR handle_image")
        await bot.send_message(chat_id=GROUP_ID, text=f"❌ {datetime.datetime.now().strftime('%Y-%m-%d')} – Erreur analyse OCR", message_thread_id=update.message.message_thread_id)

# --- ROUTE WEBHOOK ---
@app.post("/webhook")
async def webhook(req: Request):
    try:
        await telegram_ready.wait()
        raw = await req.body()
        update = Update.de_json(json.loads(raw), bot)
        await telegram_app.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.exception("❌ Erreur route /webhook")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})

# --- HANDLERS ---
telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_image))

async def log_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"📥 Message brut reçu : {update.to_dict()}")
    if update.message:
        logger.info(f"🧠 message_thread_id détecté : {getattr(update.message, 'message_thread_id', 'None')}")

general_handler = MessageHandler(filters.ALL, log_all_messages)
telegram_app.add_handler(general_handler)
