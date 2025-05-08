#!/usr/bin/env python3
import json
import io
import re
import datetime
import logging
import os
import traceback 
from difflib import get_close_matches

from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from PIL import Image
import gspread
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.cloud import vision
import uvicorn

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
RAILWAY_PUBLIC_URL = os.getenv("RAILWAY_PUBLIC_URL") 
PORT = int(os.getenv("PORT", "8000"))

google_creds_gspread_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_GSPREAD")
sheet = None
if not google_creds_gspread_json_str:
    logger.error("La variable d_environnement GOOGLE_APPLICATION_CREDENTIALS_GSPREAD n_est pas définie.")
else:
    try:
        creds_gspread_dict = json.loads(google_creds_gspread_json_str)
        gspread_creds = ServiceAccountCredentials.from_service_account_info(creds_gspread_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        gc = gspread.authorize(gspread_creds)
        sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
        logger.info("Connexion à Google Sheets réussie.")
    except Exception as e:
        logger.error(f"Erreur lors de l_initialisation de Google Sheets: {e}")
        logger.error(traceback.format_exc())

google_creds_vision_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
vision_client = None
if not google_creds_vision_json_str:
    logger.error("La variable d_environnement GOOGLE_APPLICATION_CREDENTIALS (pour Vision) n_est pas définie.")
else:
    try:
        creds_vision_dict = json.loads(google_creds_vision_json_str)
        vision_creds = ServiceAccountCredentials.from_service_account_info(creds_vision_dict)
        vision_client = vision.ImageAnnotatorClient(credentials=vision_creds)
        logger.info("Client Google Vision AI initialisé avec succès.")
    except Exception as e:
        logger.error(f"Erreur lors de l_initialisation de Google Vision AI: {e}")
        logger.error(traceback.format_exc())

app = FastAPI()
ptb_application = Application.builder().token(TOKEN).build()
already_processed = set()

with open("known_handles.json", "r", encoding="utf-8") as f:
    KNOWN_HANDLES = json.load(f)

def corriger_username(username: str, reseau: str) -> str:
    if reseau == "instagram" and username.startswith("@"):
        return username[1:]
    return username

def normaliser_nombre_followers(nombre_str: str) -> str | None:
    if not isinstance(nombre_str, str):
        return None
    nombre_str_test = nombre_str.strip()
    if not re.match(r"^[\d.,\s]*[kKm]?$", nombre_str_test, re.IGNORECASE):
        logger.debug(f"normaliser_nombre_followers: 	'{nombre_str_test}' ne correspond pas au format attendu.")
        return None

    nombre_str_cleaned_spaces = nombre_str_test.replace(" ", "")
    nombre_str_clean = nombre_str_cleaned_spaces.lower().replace(".", "").replace(",", "")
    valeur = None
    try:
        if "k" in nombre_str_clean:
            nombre_str_clean = re.sub(r"(\d)\s+k", r"\1k", nombre_str_clean)
            if not re.match(r"^\d+k$", nombre_str_clean):
                logger.debug(f"normaliser_nombre_followers: Format 'k' invalide pour 	'{nombre_str_clean}'")
                return None
            valeur = str(int(float(nombre_str_clean.replace("k", "")) * 1000))
        elif "m" in nombre_str_clean:
            nombre_str_clean = re.sub(r"(\d)\s+m", r"\1m", nombre_str_clean)
            if not re.match(r"^\d+m$", nombre_str_clean):
                logger.debug(f"normaliser_nombre_followers: Format 'm' invalide pour 	'{nombre_str_clean}'")
                return None
            valeur = str(int(float(nombre_str_clean.replace("m", "")) * 1000000))
        else:
            if not nombre_str_clean.isdigit():
                logger.debug(f"normaliser_nombre_followers: 	'{nombre_str_clean}' n_est pas un digit après nettoyage.")
                return None
            valeur = str(int(nombre_str_clean))
    except ValueError:
        logger.warning(f"normaliser_nombre_followers: ValueError lors de la conversion de 	'{nombre_str_clean}'")
        return None
    return valeur

def fusionner_nombres_adjacents(number_annotations_list_input, reseau_nom_log="inconnu"):
    if not number_annotations_list_input or len(number_annotations_list_input) < 2:
        return number_annotations_list_input

    logger.info(f"fusionner_nombres_adjacents ({reseau_nom_log}): Début de la fusion. Nombres entrants: {len(number_annotations_list_input)}")
    sorted_numbers = sorted(number_annotations_list_input, key=lambda ann: (ann['avg_y'], ann['avg_x']))
    
    merged_numbers_final = []
    processed_indices = set()
    
    i = 0
    while i < len(sorted_numbers):
        if i in processed_indices:
            i += 1
            continue

        current_ann_data = sorted_numbers[i]
        current_text_original = current_ann_data['annotation'].description
        logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}): Traitement du bloc {i}: '{current_text_original}' à y={current_ann_data['avg_y']:.2f}, x={current_ann_data['avg_x']:.2f}")

        if i + 1 < len(sorted_numbers) and (i + 1) not in processed_indices:
            next_ann_data = sorted_numbers[i+1]
            next_text_original = next_ann_data['annotation'].description
            logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}):  Vérification avec bloc suivant {i+1}: '{next_text_original}' à y={next_ann_data['avg_y']:.2f}, x={next_ann_data['avg_x']:.2f}")

            y_diff_merge = abs(current_ann_data['avg_y'] - next_ann_data['avg_y'])
            current_vertices = current_ann_data['annotation'].bounding_poly.vertices
            next_vertices = next_ann_data['annotation'].bounding_poly.vertices
            current_end_x = max(v.x for v in current_vertices)
            next_start_x = min(v.x for v in next_vertices)
            x_gap = next_start_x - current_end_x
            
            Y_THRESHOLD_MERGE = 25
            X_GAP_THRESHOLD_MERGE = 45
            X_OVERLAP_THRESHOLD_MERGE = -10

            logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}):    y_diff_merge={y_diff_merge:.2f} (Seuil Y: {Y_THRESHOLD_MERGE})")
            logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}):    x_gap={x_gap:.2f} (Seuil X: {X_OVERLAP_THRESHOLD_MERGE} <= gap < {X_GAP_THRESHOLD_MERGE})")

            is_current_simple_num_text = bool(re.fullmatch(r"\d+", current_text_original.strip()))
            is_next_simple_num_text = bool(re.fullmatch(r"\d+", next_text_original.strip()))
            logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}):    '{current_text_original}' est num simple: {is_current_simple_num_text}, '{next_text_original}' est num simple: {is_next_simple_num_text}")

            if (is_current_simple_num_text and is_next_simple_num_text and
                y_diff_merge < Y_THRESHOLD_MERGE and 
                X_OVERLAP_THRESHOLD_MERGE <= x_gap < X_GAP_THRESHOLD_MERGE):
                
                combined_text_original = f"{current_text_original}{next_text_original}"
                combined_normalized = normaliser_nombre_followers(combined_text_original)
                
                if combined_normalized:
                    logger.info(f"fusionner_nombres_adjacents ({reseau_nom_log}): FUSION RÉUSSIE de '{current_text_original}' et '{next_text_original}' -> '{combined_text_original}' (normalisé: {combined_normalized})")
                    merged_ann_entry = {
                        "text": combined_text_original.lower().strip(), 
                        "normalized": combined_normalized,
                        "avg_y": current_ann_data['avg_y'], 
                        "avg_x": current_ann_data['avg_x'], 
                        "annotation": current_ann_data['annotation'] 
                    }
                    merged_numbers_final.append(merged_ann_entry)
                    processed_indices.add(i)
                    processed_indices.add(i+1)
                    i += 2 
                    continue 
                else:
                    logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}):    Texte combiné '{combined_text_original}' non normalisable.")
            else:
                logger.debug(f"fusionner_nombres_adjacents ({reseau_nom_log}):    Critères de fusion non remplis.")
        
        merged_numbers_final.append(current_ann_data)
        processed_indices.add(i)
        i += 1
            
    logger.info(f"fusionner_nombres_adjacents ({reseau_nom_log}): Fin de la fusion. Nombres sortants: {len(merged_numbers_final)}")
    return merged_numbers_final

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("--- Entrée dans handle_photo ---")
    assistant = "INCONNU"
    today = datetime.datetime.now().strftime("%d/%m/%Y")
    message_status_general = None 
    donnees_extraites_ok = False
    action_tentee = False

    try:
        message = update.message
        if not message or not message.photo:
            logger.info("handle_photo: Message None ou sans photo. Aucune action.")
            return

        reply = message.reply_to_message
        if not reply or not hasattr(reply, 'forum_topic_created') or not reply.forum_topic_created:
            logger.info("handle_photo: Le message n'est pas une réponse à un message de création de sujet de forum. Aucune action.")
            return
        
        topic_name_for_error_handling = reply.forum_topic_created.name
        assistant = topic_name_for_error_handling
        logger.info(f"handle_photo: Traitement pour l'assistant: {assistant}")

        file_id = message.photo[-1].file_id
        if file_id in already_processed:
            logger.info(f"handle_photo: Image {file_id} déjà traitée. Ignorée.")
            return
        already_processed.add(file_id)

        new_file = await ptb_application.bot.get_file(file_id)
        img_bytes = await new_file.download_as_bytearray()
        
        image_pil = Image.open(io.BytesIO(img_bytes))
        width, height = image_pil.size
        
        crop_height_ratio = 0.4 
        temp_reseau_detect = "instagram"
        if assistant:
            assistant_lower = assistant.lower()
            if "twitter" in assistant_lower or " x " in assistant_lower:
                temp_reseau_detect = "twitter"
            elif "tiktok" in assistant_lower:
                temp_reseau_detect = "tiktok"

        if temp_reseau_detect == "twitter":
            crop_height_ratio = 0.65
            logger.info(f"handle_photo: Ratio de crop ajusté à {crop_height_ratio} pour Twitter (détecté via nom assistant).")
        
        cropped_image = image_pil.crop((0, 0, width, int(height * crop_height_ratio)))
        byte_arr = io.BytesIO()
        cropped_image.save(byte_arr, format='PNG')
        content_vision = byte_arr.getvalue()

        if not vision_client:
            logger.error("handle_photo: Client Google Vision AI non initialisé. Impossible de traiter l'image.")
            message_status_general = f"Erreur interne: Client Vision AI non disponible pour {assistant}."
            raise Exception("Client Vision AI non initialisé") 
            
        image_for_vision = vision.Image(content=content_vision)
        response = vision_client.document_text_detection(image=image_for_vision) 
        texts_annotations_vision = response.text_annotations

        if response.error.message:
            logger.error(f"handle_photo: Erreur API Google Vision: {response.error.message}")
            message_status_general = f"Erreur OCR Google Vision pour {assistant}: {response.error.message}"
        elif not texts_annotations_vision or not texts_annotations_vision[0].description:
            logger.warning(f"handle_photo: OCR n'a retourné aucun texte pour {assistant}.")
            message_status_general = f"L_OCR n'a retourné aucun texte pour l'image de {assistant}."
        else:
            ocr_text_full = texts_annotations_vision[0].description
            logger.info(f"🔍 OCR Google Vision brut (premiers 500 caractères) pour {assistant}:\n{ocr_text_full[:500]}")

            ocr_lower = ocr_text_full.lower()
            if "tiktok" in ocr_lower or "j_aime" in ocr_lower or "j’aime" in ocr_lower:
                reseau = "tiktok"
            elif "twitter" in ocr_lower or "tweets" in ocr_lower or "reposts" in ocr_lower or "abonnements" in ocr_lower or "abonnés" in ocr_lower:
                reseau = "twitter"
            elif "instagram" in ocr_lower or "publications" in ocr_lower or "getallmylinks.com" in ocr_lower or "modifier le profil" in ocr_lower:
                reseau = "instagram"
            elif "threads" in ocr_lower:
                reseau = "threads"
            elif "beacons.ai" in ocr_lower:
                if "twitter" in ocr_lower:
                    reseau = "twitter"
                else:
                    reseau = "instagram"
            else: 
                reseau = temp_reseau_detect
                logger.info(f"Réseau non clairement identifié par OCR, déduit de/mis par défaut à: {reseau}")
            logger.info(f"handle_photo: Réseau identifié pour {assistant}: {reseau}")

            usernames_found = re.findall(r"@([a-zA-Z0-9_.-]{3,})", ocr_text_full)
            reseau_handles = KNOWN_HANDLES.get(reseau.lower(), [])
            username = "Non trouvé"
            cleaned_usernames = [re.sub(r"[^a-zA-Z0-9_.-]", "", u).lower() for u in usernames_found]
            for u_cleaned in cleaned_usernames:
                if u_cleaned in [h.lower() for h in reseau_handles]:
                    username = next((h_original for h_original in reseau_handles if h_original.lower() == u_cleaned), "Non trouvé")
                    if username != "Non trouvé": break
            if username == "Non trouvé":
                for u in usernames_found:
                    matches = get_close_matches(u.lower(), reseau_handles, n=1, cutoff=0.7)
                    if matches: username = matches[0]; break
            if username == "Non trouvé" and usernames_found: username = usernames_found[0]
            urls = re.findall(r"(getallmylinks\.com|beacons\.ai|linktr\.ee|tiktok\.com)/([a-zA-Z0-9_.-]+)", ocr_text_full, re.IGNORECASE)
            if username == "Non trouvé" and urls:
                for _, u_from_url in urls:
                    match_url = get_close_matches(u_from_url.lower(), reseau_handles, n=1, cutoff=0.7)
                    if match_url: username = match_url[0]; break
                    if username == "Non trouvé": username = u_from_url 
            username = corriger_username(username, reseau)
            logger.info(f"🕵️ Username final pour {assistant}: 	'{username}' (réseau : {reseau})")

            abonnés = None
            if reseau == "tiktok":
                mots_cles_tiktok = ["followers", "abonnés", "abonné", "fans", "abos"]
                abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_tiktok, f"tiktok ({assistant})")
            elif reseau == "instagram":
                mots_cles_instagram = ["followers", "abonnés", "abonné", "suivi(e)s", "suivis"]
                abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_instagram, f"instagram ({assistant})")
            elif reseau == "twitter":
                mots_cles_twitter = ["abonnés", "abonné", "followers", "suivies", "suivis", "abonnements"]
                abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_twitter, f"twitter ({assistant})")
            elif reseau == "threads":
                 mots_cles_threads = ["followers", "abonnés", "abonné"]
                 abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_threads, f"threads ({assistant})")
            else:
                mots_cles_generiques = ["followers", "abonnés", "abonné", "fans", "suivi(e)s", "suivis"]
                abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_generiques, f"générique ({reseau}, {assistant})")

            logger.info(f"handle_photo: Abonnés extraits pour {assistant} ({reseau}): {abonnés}")

            if username != "Non trouvé" and abonnés is not None:
                donnees_extraites_ok = True
                action_tentee = True
                try:
                    if sheet: 
                        sheet.append_row([today, assistant, reseau, username, abonnés, ""])
                        logger.info(f"Données ajoutées à Google Sheets pour {assistant}: {today}, {reseau}, {username}, {abonnés}")
                        message_status_general = f"OK ✅ {username} ({reseau}) -> {abonnés} followers."
                    else:
                        logger.warning(f"Google Sheets non disponible. Données non enregistrées pour {assistant}.")
                        message_status_general = f"OK (Sheets OFF) ✅ {username} ({reseau}) -> {abonnés} followers."
                except Exception as e_gsheet:
                    logger.error(f"Erreur lors de l'écriture sur Google Sheets pour {assistant}: {e_gsheet}")
                    logger.error(traceback.format_exc())
                    message_status_general = f"⚠️ Erreur Sheets pour {assistant} ({username}, {reseau}, {abonnés}). Détails dans les logs."
            else:
                action_tentee = True
                logger.warning(f"handle_photo: Données incomplètes pour {assistant}. Username: {username}, Abonnés: {abonnés}")
                message_status_general = f"❓ Données incomplètes pour {assistant}. Username: 	'{username}'	, Abonnés: 	'{abonnés}'	. Réseau: {reseau}. OCR brut: {ocr_text_full[:150]}..."

    except Exception as e:
        logger.error(f"❌ Erreur globale dans handle_photo pour l'assistant {assistant}:")
        logger.error(traceback.format_exc())
        message_status_general = f"🆘 Erreur critique bot pour {assistant}. Détails dans les logs."
    
    finally:
        if message_status_general and GROUP_ID and hasattr(message, 'message_thread_id'): 
            try:
                await ptb_application.bot.send_message(chat_id=GROUP_ID, text=message_status_general, message_thread_id=message.message_thread_id)
                logger.info(f"Message de statut envoyé au groupe pour {assistant}: {message_status_general}")
            except Exception as e_send_status:
                logger.error(f"Erreur lors de l'envoi du message de statut au groupe pour {assistant}: {e_send_status}")
                logger.error(traceback.format_exc())
        elif not message_status_general and action_tentee:
            logger.warning(f"handle_photo: Un message de statut aurait dû être généré pour {assistant} mais ne l'a pas été.")
        elif not action_tentee:
            logger.info("handle_photo: Aucune action de traitement OCR n'a été tentée (probablement image déjà traitée ou non pertinente).")

ptb_application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

@app.post("/") 
async def webhook_handler_post(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, ptb_application.bot)
        await ptb_application.process_update(update)
        return {"status": "ok"}
    except json.JSONDecodeError:
        logger.error("telegram_webhook: Erreur de décodage JSON.")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.error(f"telegram_webhook: Erreur lors du traitement de la mise à jour: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error processing update"} 

@app.get("/") 
async def root():
    return {"message": "FastAPI server is running"}

@app.on_event("startup")
async def startup_event():
    try:
        await ptb_application.initialize() # CORRECTION: Initialisation de l'application PTB
        base_url = RAILWAY_PUBLIC_URL.rstrip("/")
        webhook_url = f"{base_url}/" 
        await ptb_application.bot.set_webhook(url=webhook_url, allowed_updates=["message"])
        logger.info(f"Webhook configuré sur: {webhook_url}")
    except Exception as e:
        logger.error(f"Erreur lors de la configuration du webhook: {e}")
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)

