import json # Ajout de l'import manquant
import io
import re
import datetime
import logging
import os # Ajout pour les variables d'environnement
from difflib import get_close_matches

from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from PIL import Image, ImageOps
import gspread
from google.oauth2.service_account import Credentials # Modifié pour gspread v6+
from google.cloud import vision # Ajout pour Google Vision AI

# Configuration du logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration initiale ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# Authentification Google Sheets (adapté pour gspread v6+ et variables d'environnement)
google_creds_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_GSPREAD") # Nom de variable distinct pour gspread
if not google_creds_json_str:
    logger.error("La variable d'environnement GOOGLE_APPLICATION_CREDENTIALS_GSPREAD n'est pas définie.")
    # Gérer l'erreur ou quitter
    exit()

scopes = ["https://www.googleapis.com/auth/spreadsheets"]
creds_dict = json.loads(google_creds_json_str)
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SPREADSHEET_ID).sheet1

# Initialisation du client Google Vision AI
# GOOGLE_APPLICATION_CREDENTIALS est utilisé automatiquement par la bibliothèque cliente si défini
vision_client = vision.ImageAnnotatorClient()

bot = Bot(TOKEN)
already_processed = set()

# Charger les pseudos connus
with open("known_handles.json", "r", encoding="utf-8") as f:
    KNOWN_HANDLES = json.load(f)

def corriger_username(username: str, reseau: str) -> str:
    # (Logique de correction inchangée)
    if reseau == "instagram" and username.startswith("@"):
        return username[1:]
    return username

# --- Extraction followers dédiée TikTok ---
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
                elif "m" in mot.lower(): # Ajout pour les millions
                    mot_clean = mot_clean.replace("m", "")
                    nombre = float(mot_clean) * 1000000
                else:
                    nombre = float(mot_clean)
                nombres.append(int(nombre))
            except:
                continue

    if len(nombres) >= 2:
        return str(nombres[1])  # 2e bloc numérique = followers
    elif len(nombres) == 1: # Au cas où seul le nombre de followers est détecté
        return str(nombres[0])
    return None

# --- handle_photo ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = update.message
        if not message or not message.photo:
            return

        # (Logique de vérification du topic et de l'assistant inchangée)
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
        # Conserver le recadrage pour optimiser l'analyse OCR
        # Le recadrage à 40% de la hauteur est conservé
        cropped_image = image.crop((0, 0, width, int(height * 0.4)))
        enhanced_image = ImageOps.autocontrast(cropped_image)

        # Sauvegarder l'image améliorée en bytes pour Google Vision
        byte_arr = io.BytesIO()
        enhanced_image.save(byte_arr, format='PNG') # PNG est un bon format sans perte
        content_vision = byte_arr.getvalue()

        # Appel à Google Vision AI
        image_vision = vision.Image(content=content_vision)
        response = vision_client.text_detection(image=image_vision)
        texts = response.text_annotations

        if response.error.message:
            raise Exception(
                f"{response.error.message}\nPour plus d'informations, visitez https://cloud.google.com/apis/design/errors"
            )

        ocr_text = ""
        if texts:
            ocr_text = texts[0].description # Le premier élément est le texte complet
        
        logger.info(f"🔍 OCR Google Vision brut :\n{ocr_text}")

        # (Logique d'identification du réseau, extraction username et abonnés inchangée,
        # mais elle utilisera 'ocr_text' provenant de Google Vision)

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
            # Par défaut ou si non clairement identifiable, on pourrait mettre une logique plus fine
            # ou laisser comme avant
            reseau = "instagram" 
            logger.info("Réseau non clairement identifié, par défaut Instagram.")

        usernames_found = re.findall(r"@([a-zA-Z0-9_.-]{3,})", ocr_text) # étendu pour inclure '.' et '-' 
        reseau_handles = KNOWN_HANDLES.get(reseau.lower(), [])
        username = "Non trouvé"
        
        # Amélioration de la recherche de username
        # 1. Recherche exacte (après nettoyage) parmi les handles connus
        cleaned_usernames = [re.sub(r'[^a-zA-Z0-9_.-]', '', u).lower() for u in usernames_found]
        for u_cleaned in cleaned_usernames:
            if u_cleaned in [h.lower() for h in reseau_handles]:
                # Retrouver le handle original pour la casse
                for h_original in reseau_handles:
                    if h_original.lower() == u_cleaned:
                        username = h_original
                        break
                if username != "Non trouvé":
                    break
        
        # 2. Si pas trouvé, recherche avec get_close_matches
        if username == "Non trouvé":
            for u in usernames_found:
                matches = get_close_matches(u.lower(), reseau_handles, n=1, cutoff=0.7) # Cutoff un peu plus strict
                if matches:
                    username = matches[0]
                    break
        
        # 3. Si toujours pas trouvé et qu'il y a des candidats, prendre le premier (ou le plus long, ou autre heuristique)
        if username == "Non trouvé" and usernames_found:
            username = usernames_found[0] # Peut être affiné

        # (Logique d'extraction des URLs et username à partir des URLs inchangée)
        urls = re.findall(r"(getallmylinks\.com|beacons\.ai|linktr\.ee|tiktok\.com)/([a-zA-Z0-9_.-]+)", ocr_text, re.IGNORECASE)
        if username == "Non trouvé" and urls: # Tenter via URL si username non trouvé
            for _, u_from_url in urls:
                # Essayer de matcher avec les handles connus
                match_url = get_close_matches(u_from_url.lower(), reseau_handles, n=1, cutoff=0.7)
                if match_url:
                    username = match_url[0]
                    break
                # Sinon, prendre le username de l'URL tel quel
                if username == "Non trouvé": # S'assurer qu'on ne l'a pas déjà trouvé
                     username = u_from_url

        username = corriger_username(username, reseau)
        logger.info(f"🕵️ Username final : '{username}' (réseau : {reseau})")

        abonnés = None
        if reseau == "tiktok":
            abonnés = extraire_followers_tiktok(ocr_text)
        else:
            # Logique d'extraction des abonnés pour Instagram/Twitter/Threads
            # Essayer de trouver "XXX Abonnés" ou "XXX Followers"
            # Le regex doit être robuste aux variations (espaces, majuscules, etc.)
            # Priorité aux chiffres explicitement suivis de "abonnés" ou "followers"
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
                # Logique des trois blocs de chiffres (moins prioritaire)
                numbers_extracted = []
                raw_numbers = re.findall(r"(\d+(?:[.,]\d+)?(?:[kKmM]?))", ocr_text)
                for num_str in raw_numbers:
                    val = num_str.lower().replace(",", ".") # Normaliser virgule
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

