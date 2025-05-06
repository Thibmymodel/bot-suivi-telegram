import json
import io
import re
import datetime
import logging
import os
from difflib import get_close_matches

from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from PIL import Image, ImageOps
import gspread
from google.oauth2.service_account import Credentials as ServiceAccountCredentials # Renommé pour clarté
from google.cloud import vision

# Configuration du logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration initiale ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# Authentification Google Sheets
google_creds_gspread_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_GSPREAD")
if not google_creds_gspread_json_str:
    logger.error("La variable d'environnement GOOGLE_APPLICATION_CREDENTIALS_GSPREAD n'est pas définie.")
    exit()
try:
    creds_gspread_dict = json.loads(google_creds_gspread_json_str)
    gspread_creds = ServiceAccountCredentials.from_service_account_info(creds_gspread_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(gspread_creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
except Exception as e:
    logger.error(f"Erreur lors de l'initialisation de Google Sheets: {e}")
    exit()

# Initialisation du client Google Vision AI
google_creds_vision_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if not google_creds_vision_json_str:
    logger.error("La variable d'environnement GOOGLE_APPLICATION_CREDENTIALS (pour Vision) n'est pas définie.")
    exit()
try:
    creds_vision_dict = json.loads(google_creds_vision_json_str)
    vision_creds = ServiceAccountCredentials.from_service_account_info(creds_vision_dict)
    vision_client = vision.ImageAnnotatorClient(credentials=vision_creds)
except Exception as e:
    logger.error(f"Erreur lors de l'initialisation de Google Vision AI: {e}")
    exit()

bot = Bot(TOKEN)
already_processed = set()

with open("known_handles.json", "r", encoding="utf-8") as f:
    KNOWN_HANDLES = json.load(f)

def corriger_username(username: str, reseau: str) -> str:
    if reseau == "instagram" and username.startswith("@"):
        return username[1:]
    return username

def extraire_followers_tiktok(texte_ocr: str) -> str | None:
    lignes = texte_ocr.replace(",", ".").split()
    nombres = []
    for mot in lignes:
        mot_clean = re.sub(r"[^\d.]", "", mot)
        if mot_clean:
            try:
                if "k" in mot.lower():
                    mot_clean = mot_clean.replace("k", "")
                    nombre = float(mot_clean) * 1000
                elif "m" in mot.lower():
                    mot_clean = mot_clean.replace("m", "")
                    nombre = float(mot_clean) * 1000000
                else:
                    nombre = float(mot_clean)
                nombres.append(int(nombre))
            except:
                continue
    if len(nombres) >= 2:
        return str(nombres[1])
    elif len(nombres) == 1:
        return str(nombres[0])
    return None

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = update.message
        if not message or not message.photo:
            return

        reply = message.reply_to_message
        if not reply or not hasattr(reply, "forum_topic_created"):
            logger.info("Message n'est pas une réponse à la création d'un topic.")
            return

        topic_name = reply.forum_topic_created.name
        if not topic_name.startswith("SUIVI "):
            logger.info(f"Nom du topic '{topic_name}' ne commence pas par 'SUIVI '.")
            return
        assistant = topic_name.replace("SUIVI ", "").strip().upper()

        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        img_bytes_io = io.BytesIO()
        await file.download_to_memory(img_bytes_io)
        img_bytes_io.seek(0)
        img_content = img_bytes_io.read()

        image = Image.open(io.BytesIO(img_content))
        width, height = image.size
        cropped_image = image.crop((0, 0, width, int(height * 0.4)))
        enhanced_image = ImageOps.autocontrast(cropped_image)

        byte_arr = io.BytesIO()
        enhanced_image.save(byte_arr, format='PNG')
        content_vision = byte_arr.getvalue()

        image_vision = vision.Image(content=content_vision)
        response = vision_client.text_detection(image=image_vision)
        texts = response.text_annotations

        if response.error.message:
            raise Exception(
                f"{response.error.message}\nPour plus d'informations, visitez https://cloud.google.com/apis/design/errors"
            )

        ocr_text = ""
        if texts:
            ocr_text = texts[0].description
        
        logger.info(f"🔍 OCR Google Vision brut :\n{ocr_text}")

        if "getallmylinks.com" in ocr_text.lower():
            reseau = "instagram"
        elif "beacons.ai" in ocr_text.lower():
            reseau = "twitter"
        elif "tiktok" in ocr_text.lower() or any(k in ocr_text.lower() for k in ["followers", "j'aime", "abonnés", "abonné"]):
            reseau = "tiktok"
        elif "threads" in ocr_text.lower():
            reseau = "threads"
        elif any(x in ocr_text.lower() for x in ["modifier le profil", "suivi(e)s", "publications"]):
            reseau = "instagram"
        else:
            reseau = "instagram" 
            logger.info("Réseau non clairement identifié, par défaut Instagram.")

        usernames_found = re.findall(r"@([a-zA-Z0-9_.-]{3,})", ocr_text)
        reseau_handles = KNOWN_HANDLES.get(reseau.lower(), [])
        username = "Non trouvé"
        
        cleaned_usernames = [re.sub(r'[^a-zA-Z0-9_.-]', '', u).lower() for u in usernames_found]
        for u_cleaned in cleaned_usernames:
            if u_cleaned in [h.lower() for h in reseau_handles]:
                for h_original in reseau_handles:
                    if h_original.lower() == u_cleaned:
                        username = h_original
                        break
                if username != "Non trouvé":
                    break
        
        if username == "Non trouvé":
            for u in usernames_found:
                matches = get_close_matches(u.lower(), reseau_handles, n=1, cutoff=0.7)
                if matches:
                    username = matches[0]
                    break
        
        if username == "Non trouvé" and usernames_found:
            username = usernames_found[0]

        urls = re.findall(r"(getallmylinks\.com|beacons\.ai|linktr\.ee|tiktok\.com)/([a-zA-Z0-9_.-]+)", ocr_text, re.IGNORECASE)
        if username == "Non trouvé" and urls:
            for _, u_from_url in urls:
                match_url = get_close_matches(u_from_url.lower(), reseau_handles, n=1, cutoff=0.7)
                if match_url:
                    username = match_url[0]
                    break
                if username == "Non trouvé":
                     username = u_from_url

        username = corriger_username(username, reseau)
        logger.info(f"🕵️ Username final : '{username}' (réseau : {reseau})")

        abonnés = None
        if reseau == "tiktok":
            abonnés = extraire_followers_tiktok(ocr_text)
        else:
            match_explicit = re.search(r"(\d{1,3}(?:[ .,kKmM]?\d{1,3})*)\s*(?:abonnés|followers|suivies|suivi\(e\)s)", ocr_text, re.IGNORECASE)
            if match_explicit:
                abonnés_str = match_explicit.group(1).lower()
                abonnés_str = abonnés_str.replace(" ", "").replace(".", "").replace(",", "")
                if "k" in abonnés_str:
                    abonnés = str(int(float(abonnés_str.replace("k", "")) * 1000))
                elif "m" in abonnés_str:
                    abonnés = str(int(float(abonnés_str.replace("m", "")) * 1000000))
                else:
                    abonnés = abonnés_str
            
            if not abonnés:
                numbers_extracted = []
                raw_numbers = re.findall(r"(\d+(?:[.,]\d+)?(?:[kKmM]?))", ocr_text)
                for num_str in raw_numbers:
                    val = num_str.lower().replace(",", ".")
                    multiplier = 1
                    if "k" in val:
                        multiplier = 1000
                        val = val.replace("k", "")
                    elif "m" in val:
                        multiplier = 1000000
                        val = val.replace("m", "")
                    try:
                        numbers_extracted.append(int(float(val) * multiplier))
                    except ValueError:
                        continue 
                
                logger.info(f"Nombres extraits pour analyse abonnés: {numbers_extracted}")

                if len(numbers_extracted) >= 3:
                     abonnés = str(numbers_extracted[1]) 
                elif len(numbers_extracted) == 2 and reseau == "instagram":
                     abonnés = str(numbers_extracted[1])
                elif len(numbers_extracted) == 1 and reseau == "instagram": 
                     abonnés = str(numbers_extracted[0])

        if not username or username == "Non trouvé" or not abonnés:
            logger.error(f"Erreur: Nom d'utilisateur ('{username}') ou abonnés ('{abonnés}') introuvable. OCR: {ocr_text[:500]}")
            pass 

        if message.message_id in already_processed:
            logger.info("⚠️ Message déjà traité, on ignore.")
            return
        already_processed.add(message.message_id)

        today = datetime.datetime.now().strftime("%d/%m/%Y")
        username_to_sheet = f"@{username}" if username and username != "Non trouvé" else ""
        abonnés_to_sheet = str(abonnés) if abonnés else ""

        row = [today, assistant, reseau, username_to_sheet, abonnés_to_sheet, ""]
        sheet.append_row(row)

        msg = f"📊 {today} - {assistant} - {reseau.capitalize()} @{username if username and username != 'Non trouvé' else 'N/A'} ({abonnés if abonnés else 'N/A'}) ajouté ✅"
        if not username or username == "Non trouvé" or not abonnés:
            msg = f"⚠️ {today} - {assistant} - Données incomplètes pour {reseau.capitalize()}. OCR: {ocr_text[:100]}... Ajout partiel. ✅"
        
        await bot.send_message(chat_id=GROUP_ID, text=msg, message_thread_id=message.message_thread_id if message.is_topic_message else None)

    except Exception as e:
        logger.exception("❌ Erreur traitement handle_photo")
        error_message = f"❌ {datetime.datetime.now().strftime('%d/%m')} - Erreur analyse: {str(e)[:100]}"
        try:
            thread_id_for_error = message.message_thread_id if message and message.is_topic_message else None
            await bot.send_message(chat_id=GROUP_ID, text=error_message, message_thread_id=thread_id_for_error)
        except Exception as send_error:
            logger.error(f"Impossible d'envoyer le message d'erreur au groupe: {send_error}")

from fastapi import FastAPI, Request, HTTPException
import asyncio
import uvicorn

app = FastAPI(lifespan=None)

@app.on_event("startup")
async def startup():
    logger.info("Application startup...")
    mode_polling = os.getenv("MODE_POLLING", "false").lower()
    if mode_polling != "true":
        webhook_url = os.getenv("RAILWAY_PUBLIC_URL")
        if webhook_url:
            if not webhook_url.endswith("/webhook"):
                 webhook_url += "/webhook"
            logger.info(f"Setting webhook to: {webhook_url}")
            await bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
            logger.info("Webhook set.")
        else:
            logger.warning("RAILWAY_PUBLIC_URL not set, webhook not configured.")
    else:
        logger.info("Mode polling activé, pas de configuration de webhook.")
        pass 

@app.post("/webhook")
async def webhook_handler(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        # Utilisation de l'application PTB pour créer un contexte si disponible, sinon contexte simple
        # Pour une intégration complète, l'objet `application` de PTB devrait être accessible ici.
        # Dans ce cas, on crée un contexte simple.
        context = ContextTypes.DEFAULT_TYPE(application=None, chat_id=update.effective_chat.id if update.effective_chat else None, user_id=update.effective_user.id if update.effective_user else None)
        await handle_photo(update, context)
        return {"status": "ok"}
    except json.JSONDecodeError:
        logger.error("Error decoding JSON from webhook")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.exception("Error processing webhook")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    mode_polling = os.getenv("MODE_POLLING", "false").lower()
    if mode_polling == "true":
        logger.info("Lancement en mode polling...")
        application = Application.builder().token(TOKEN).build()
        application.add_handler(MessageHandler(filters.PHOTO & (~filters.COMMAND), handle_photo))
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    else:
        logger.info("Lancement en mode webhook avec Uvicorn (localement)...")
        port = int(os.getenv("PORT", 8000))
        uvicorn.run(app, host="0.0.0.0", port=port)

