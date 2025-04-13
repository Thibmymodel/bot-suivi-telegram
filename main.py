# main.py
import os
import json
import logging
from datetime import datetime
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import (
    Application, ApplicationBuilder, ContextTypes, MessageHandler, filters
)
from telegram.ext import JobQueue
import pytesseract
from PIL import Image, ImageEnhance, ImageOps
import gspread
from google.oauth2.service_account import Credentials

# === CONFIG ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID = -1002317321058
REPLY_DELAY = 3  # minutes
WEBHOOK_PATH = "/webhook"

# === GOOGLE SHEET SETUP ===
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
credentials_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
credentials = Credentials.from_service_account_info(json.loads(credentials_json), scopes=SCOPES)
gc = gspread.authorize(credentials)
sheet = gc.open_by_key("1__RzRpZKj0kg8Cl0QB-D91-hGKKff9SqsOQRE0GvReE")
worksheet = sheet.worksheet("Données Journalières")

# === TEMP STORE ===
pending_images = {}

# === OCR FUNCTION ===
def try_ocr_variants(image_path):
    img = Image.open(image_path)
    variants = [
        img,
        ImageOps.grayscale(img),
        ImageEnhance.Contrast(ImageOps.grayscale(img)).enhance(2),
        ImageOps.invert(ImageOps.grayscale(img)),
        img.resize((img.size[0]*2, img.size[1]*2))
    ]
    for variant in variants:
        text = pytesseract.image_to_string(variant, lang="eng+fra")
        if any(x in text.lower() for x in ["followers", "abonnés", "suivis", "publications"]):
            return text
    return None

def extract_info_from_image(image_path):
    text = try_ocr_variants(image_path)
    if not text:
        return "Inconnu", "ECHEC OCR ❌", -1
    lines = text.split("\n")
    text_lower = text.lower()
    if "threads" in text_lower:
        network = "Threads"
    elif "tiktok" in text_lower or "j'aime" in text_lower:
        network = "TikTok"
    elif "twitter" in text_lower or "tweets" in text_lower:
        network = "Twitter"
    else:
        network = "Instagram"

    account, followers = "inconnu", -1
    for line in lines:
        if "@" in line and account == "inconnu":
            account = line.strip().split()[0]
        if any(x in line.lower() for x in ["followers", "abonnés"]):
            digits = ''.join([c if c.isdigit() or c in "kKmM.," else '' for c in line]).replace(",", ".")
            if 'k' in digits.lower():
                followers = int(float(digits.lower().replace('k','')) * 1000)
            elif 'm' in digits.lower():
                followers = int(float(digits.lower().replace('m','')) * 1_000_000)
            elif digits:
                followers = int(float(digits))
    if account == "inconnu" or followers == -1:
        return "Inconnu", "ECHEC OCR ❌", -1
    return network, account, followers

def get_previous_count(account_name):
    try:
        records = worksheet.get_all_records()
        for row in reversed(records):
            if row['Compte'] == account_name and row['Abonnés'] > 0:
                return row['Abonnés']
    except:
        pass
    return 0

# === FASTAPI + TELEGRAM ===
app_fastapi = FastAPI()
bot_app = ApplicationBuilder().token(BOT_TOKEN).build()

@bot_app.message_handler(filters.PHOTO)
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != GROUP_ID:
        return
    if not update.message.photo:
        return
    user_id = update.message.chat_id
    file = await context.bot.get_file(update.message.photo[-1].file_id)
    file_path = f"temp_{update.message.message_id}.jpg"
    await file.download_to_drive(file_path)
    logging.info(f"Image téléchargée : {file_path}")
    if user_id not in pending_images:
        pending_images[user_id] = {"files": [], "timestamp": datetime.now()}
    pending_images[user_id]["files"].append(file_path)

async def handle_pending(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for user_id in list(pending_images.keys()):
        images = pending_images[user_id]
        if (now - images["timestamp"]).total_seconds() > REPLY_DELAY * 60:
            results = []
            for file_path in images["files"]:
                res = extract_info_from_image(file_path)
                today = datetime.now().strftime("%Y-%m-%d")
                try:
                    all_rows = worksheet.get_all_records()
                    if any(r.get('Date') == today and r.get('Compte') == res[1] for r in all_rows):
                        continue
                except Exception as e:
                    print("Erreur lecture Google Sheet :", e)
                    continue
                previous = get_previous_count(res[1])
                evolution = res[2] - previous if res[2] > 0 else 0
                try:
                    worksheet.append_row([
                        today,
                        context.bot.get_chat(user_id).username or "@inconnu",
                        res[0],
                        res[1],
                        res[2],
                        evolution
                    ])
                    results.append(res)
                except Exception as e:
                    print("Erreur écriture Google Sheet :", e)
            msg = f"🤖 {datetime.now().strftime('%d/%m')} – {len(results)} comptes détectés et ajoutés ✅"
            try:
                await context.bot.send_message(chat_id=user_id, text=msg)
            except Exception as e:
                print("Erreur message Telegram :", e)
            del pending_images[user_id]

@app_fastapi.on_event("startup")
async def on_startup():
    await bot_app.initialize()
    bot_app.job_queue.run_repeating(handle_pending, interval=REPLY_DELAY * 60)
    await bot_app.start()
    logging.info("🟢 Bot Telegram prêt à recevoir les mises à jour via webhook")

@app_fastapi.post(WEBHOOK_PATH)
async def webhook_handler(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
    except Exception as e:
        logging.error(f"Erreur traitement webhook : {e}")
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    print("🚀 Lancement local du serveur webhook sur http://localhost:8000")
    uvicorn.run("main:app_fastapi", host="0.0.0.0", port=8000, reload=True)
