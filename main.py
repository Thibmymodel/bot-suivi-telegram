import json
import io
import re
import datetime
import logging
import os
import traceback # Assurez-vous que traceback est importé
from difflib import get_close_matches

from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from PIL import Image, ImageOps # ImageEnhance retiré car non utilisé
import gspread
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.cloud import vision

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
RAILWAY_PUBLIC_URL = os.getenv("RAILWAY_PUBLIC_URL")

# Initialisation Google Sheets
google_creds_gspread_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_GSPREAD")
if not google_creds_gspread_json_str:
    logger.error("La variable d_environnement GOOGLE_APPLICATION_CREDENTIALS_GSPREAD n_est pas définie.")
    exit()
try:
    creds_gspread_dict = json.loads(google_creds_gspread_json_str)
    gspread_creds = ServiceAccountCredentials.from_service_account_info(creds_gspread_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(gspread_creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
    logger.info("Connexion à Google Sheets réussie.")
except Exception as e:
    logger.error(f"Erreur lors de l_initialisation de Google Sheets: {e}")
    logger.error(traceback.format_exc())
    exit()

# Initialisation Google Vision AI
google_creds_vision_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if not google_creds_vision_json_str:
    logger.error("La variable d_environnement GOOGLE_APPLICATION_CREDENTIALS (pour Vision) n_est pas définie.")
    exit()
try:
    creds_vision_dict = json.loads(google_creds_vision_json_str)
    vision_creds = ServiceAccountCredentials.from_service_account_info(creds_vision_dict)
    vision_client = vision.ImageAnnotatorClient(credentials=vision_creds)
    logger.info("Client Google Vision AI initialisé avec succès.")
except Exception as e:
    logger.error(f"Erreur lors de l_initialisation de Google Vision AI: {e}")
    logger.error(traceback.format_exc())
    exit()

bot = Bot(TOKEN)

with open("known_handles.json", "r", encoding="utf-8") as f:
    KNOWN_HANDLES = json.load(f)

def corriger_username(username: str, reseau: str) -> str:
    if reseau == "instagram" and username.startswith("@"):
        username = username[1:]
    return username.strip()

def normaliser_nombre_followers(nombre_str: str) -> str | None:
    if not isinstance(nombre_str, str):
        return None
    nombre_str_test = nombre_str.strip()
    if not re.match(r"^[\d.,\s]*[kKm]?$", nombre_str_test, re.IGNORECASE):
        logger.debug(f"normaliser_nombre_followers: 	'{nombre_str_test}' ne correspond pas au format attendu (regex initial).")
        return None

    nombre_str_clean_pour_km = nombre_str_test.lower()
    nombre_str_clean_pour_km = re.sub(r"[\s.,]", "", nombre_str_clean_pour_km) # Enlève espaces, points, virgules pour k/m
    
    nombre_str_clean_pour_float = nombre_str_test.replace(" ", "")
    if ',' in nombre_str_clean_pour_float and '.' in nombre_str_clean_pour_float:
        if nombre_str_clean_pour_float.rfind('.') < nombre_str_clean_pour_float.rfind(','):
            nombre_str_clean_pour_float = nombre_str_clean_pour_float.replace(".","")
            nombre_str_clean_pour_float = nombre_str_clean_pour_float.replace(",",".")
        else: 
            nombre_str_clean_pour_float = nombre_str_clean_pour_float.replace(",","")
    else:
        nombre_str_clean_pour_float = nombre_str_clean_pour_float.replace(",",".")
    
    parts = nombre_str_clean_pour_float.split('.')
    if len(parts) > 1:
        # Garde la dernière partie comme décimale, joint le reste.
        # Ex: "1.234.56" -> "1234.56"
        nombre_str_clean_pour_float = "".join(parts[:-1]) + "." + parts[-1]

    valeur = None
    try:
        if "k" in nombre_str_clean_pour_km:
            valeur_km = nombre_str_clean_pour_float.lower().replace("k", "")
            if not re.match(r"^\d*\.?\d+$", valeur_km):
                 logger.debug(f"normaliser_nombre_followers: Format 'k' invalide pour 	'{valeur_km}' (après nettoyage float)")
                 return None
            valeur = str(int(float(valeur_km) * 1000))
        elif "m" in nombre_str_clean_pour_km:
            valeur_km = nombre_str_clean_pour_float.lower().replace("m", "")
            if not re.match(r"^\d*\.?\d+$", valeur_km):
                 logger.debug(f"normaliser_nombre_followers: Format 'm' invalide pour 	'{valeur_km}' (après nettoyage float)")
                 return None
            valeur = str(int(float(valeur_km) * 1000000))
        else:
            temp_val = re.sub(r"\D", "", nombre_str_test) 
            if not temp_val.isdigit():
                logger.debug(f"normaliser_nombre_followers: 	'{temp_val}' n_est pas un digit après nettoyage complet pour non k/m.")
                return None
            if not temp_val:
                logger.debug(f"normaliser_nombre_followers: Chaine vide pour 	'{nombre_str_test}' après nettoyage non k/m.")
                return None
            valeur = str(int(temp_val))
    except ValueError as e_norm:
        cleaned_for_log = re.sub(r'\D', '', nombre_str_test)
        logger.warning(f"normaliser_nombre_followers: ValueError lors de la conversion de \t'{nombre_str_test}' (nettoyé en \t'{nombre_str_clean_pour_float}' ou \t'{cleaned_for_log}'). Erreur: {e_norm}")
        return None
    return valeur

def extraire_followers_spatial(text_annotations, mots_cles_specifiques, reseau_nom="inconnu") -> str | None:
    try:
        logger.info(f"extraire_followers_spatial ({reseau_nom}): --- Début de l_extraction spatiale ---")
        keyword_annotations_list = []
        number_annotations_list = []

        if not text_annotations:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucune annotation de texte fournie.")
            return None
        
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre total d_annotations reçues: {len(text_annotations)}")
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Mots-clés cibles: {mots_cles_specifiques}")

        for i, annotation in enumerate(text_annotations[1:]): 
            try:
                if not hasattr(annotation, 'description') or not hasattr(annotation, 'bounding_poly'):
                    continue
                text_desc = annotation.description 
                text_lower = text_desc.lower().strip()
                if not hasattr(annotation.bounding_poly, 'vertices') or len(annotation.bounding_poly.vertices) < 4:
                    continue
                
                vertices = annotation.bounding_poly.vertices
                avg_y = (vertices[0].y + vertices[1].y + vertices[2].y + vertices[3].y) / 4
                avg_x = (vertices[0].x + vertices[1].x + vertices[2].x + vertices[3].x) / 4
                min_x = min(v.x for v in vertices)
                max_x = max(v.x for v in vertices)
                min_y = min(v.y for v in vertices)
                max_y = max(v.y for v in vertices)

                logger.debug(f"extraire_followers_spatial ({reseau_nom}): Ann {i}: 	'{text_desc}' (y:{avg_y:.0f}, x:{avg_x:.0f}, w:{max_x-min_x:.0f}, h:{max_y-min_y:.0f})")

                if any(keyword.lower() in text_lower for keyword in mots_cles_specifiques):
                    keyword_annotations_list.append({"text": text_lower, "avg_y": avg_y, "avg_x": avg_x, "min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y, "annotation": annotation})
                    logger.info(f"extraire_followers_spatial ({reseau_nom}): MOT-CLÉ TROUVÉ: 	'{text_lower}' à y={avg_y:.0f}, x={avg_x:.0f}")
                
                if re.search(r"\d", text_desc) and not re.fullmatch(r"\d{1,2}:\d{2}", text_desc):
                    if re.match(r"^[\d.,\s]+[kKm]?$", text_desc.replace(" ", ""), re.IGNORECASE): 
                        nombre_normalise_test = normaliser_nombre_followers(text_desc) 
                        if nombre_normalise_test:
                            number_annotations_list.append({
                                "text": text_desc, 
                                "normalized": nombre_normalise_test, 
                                "avg_y": avg_y, "avg_x": avg_x, 
                                "min_x": min_x, "max_x": max_x, 
                                "min_y": min_y, "max_y": max_y, 
                                "annotation": annotation
                            })
                            logger.info(f"extraire_followers_spatial ({reseau_nom}): NOMBRE POTENTIEL TROUVÉ: 	'{text_desc}' (normalisé: {nombre_normalise_test}) à y={avg_y:.0f}, x={avg_x:.0f}")
                        else:
                            logger.debug(f"extraire_followers_spatial ({reseau_nom}): 	'{text_desc}' non normalisable en nombre.")
                    else:
                        logger.debug(f"extraire_followers_spatial ({reseau_nom}): 	'{text_desc}' ne correspond pas au format numérique avec espaces/k/m (deuxième regex).")
            except Exception as e_loop_ann:
                logger.error(f"extraire_followers_spatial ({reseau_nom}): ERREUR INATTENDUE lors du traitement de l_annotation {i} ('{annotation.description if hasattr(annotation, 'description') else 'N/A'}'): {e_loop_ann}")
                logger.error(traceback.format_exc())
                continue 
        
        logger.info(f"extraire_followers_spatial ({reseau_nom}): --- Nombres potentiels AVANT fusion ---")
        for idx, num_ann_log in enumerate(number_annotations_list):
            logger.info(f"  Pre-fusion Num {idx}: 	'{num_ann_log['text']}' (norm: {num_ann_log['normalized']}), y:{num_ann_log['avg_y']:.0f}, x:{num_ann_log['avg_x']:.0f}")

        if len(number_annotations_list) > 1:
            logger.info(f"extraire_followers_spatial ({reseau_nom}): Tentative de regroupement de {len(number_annotations_list)} nombres.")
            number_annotations_list.sort(key=lambda ann: (ann['avg_y'], ann['avg_x']))
            merged_numbers_final = []
            temp_merged_list = list(number_annotations_list)
            
            processed_indices = set()

            for i in range(len(temp_merged_list)):
                if i in processed_indices:
                    continue

                current_num_ann = temp_merged_list[i]
                best_merged_ann = current_num_ann # Initialiser avec l'élément courant
                current_merged_indices = {i}

                # Essayer de fusionner avec les suivants
                for j in range(i + 1, len(temp_merged_list)):
                    if j in processed_indices:
                        continue
                    
                    next_num_ann_to_try = temp_merged_list[j]
                    
                    # Tenter de fusionner `best_merged_ann` (qui peut déjà être une fusion) avec `next_num_ann_to_try`
                    y_diff_merge = abs(best_merged_ann['avg_y'] - next_num_ann_to_try['avg_y'])
                    x_gap_merge = next_num_ann_to_try['min_x'] - best_merged_ann['max_x']

                    if y_diff_merge < 20 and x_gap_merge >= -10 and x_gap_merge < 50: # Critères de proximité
                        # Important: utiliser le texte original pour la fusion
                        combined_text_try = best_merged_ann['text'] + " " + next_num_ann_to_try['text']
                        combined_normalized_try = normaliser_nombre_followers(combined_text_try)
                        
                        if combined_normalized_try and len(combined_normalized_try) >= len(best_merged_ann['normalized']):
                            logger.info(f"extraire_followers_spatial ({reseau_nom}): Fusion potentielle de 	'{best_merged_ann['text']}' et 	'{next_num_ann_to_try['text']}' en 	'{combined_text_try}' (norm: {combined_normalized_try})")
                            best_merged_ann = {
                                "text": combined_text_try,
                                "normalized": combined_normalized_try,
                                "avg_y": (best_merged_ann['avg_y'] + next_num_ann_to_try['avg_y']) / 2,
                                "avg_x": (best_merged_ann['min_x'] + next_num_ann_to_try['max_x']) / 2, 
                                "min_x": best_merged_ann['min_x'],
                                "max_x": next_num_ann_to_try['max_x'],
                                "min_y": min(best_merged_ann['min_y'], next_num_ann_to_try['min_y']),
                                "max_y": max(best_merged_ann['max_y'], next_num_ann_to_try['max_y']),
                                "annotation": None 
                            }
                            current_merged_indices.add(j) # Ajouter l'index de l'élément fusionné
                        else:
                            # La fusion n'est pas meilleure ou n'est pas valide, on arrête pour ce `current_num_ann`
                            pass # Ne pas fusionner si ça ne donne pas un meilleur résultat
                    else:
                        # Pas assez proche pour fusionner avec le suivant dans la chaîne de fusion
                        pass # On pourrait décider de ne pas continuer la chaîne de fusion ici
            
                merged_numbers_final.append(best_merged_ann)
                processed_indices.update(current_merged_indices) # Marquer tous les indices utilisés dans cette fusion

            if merged_numbers_final:
                logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombres après tentative de fusion: {len(merged_numbers_final)}")
                for idx, merged_ann_log in enumerate(merged_numbers_final):
                    logger.info(f"  Post-fusion Num {idx}: 	'{merged_ann_log['text']}' (norm: {merged_ann_log['normalized']}), y:{merged_ann_log['avg_y']:.0f}, x:{merged_ann_log['avg_x']:.0f}")
                number_annotations_list = merged_numbers_final

        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre de mots-clés trouvés: {len(keyword_annotations_list)}")
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre de nombres (post-fusion final): {len(number_annotations_list)}")

        if not keyword_annotations_list:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucun mot-clé de followers trouvé.")
            if number_annotations_list:
                number_annotations_list.sort(key=lambda x: int(x.get("normalized", "0").replace(" ","")), reverse=True)
                if number_annotations_list:
                    logger.warning(f"extraire_followers_spatial ({reseau_nom}) (Fallback sans mot-clé): Sélection du plus grand nombre: {number_annotations_list[0]['normalized']}")
                    return number_annotations_list[0]['normalized']
            return None

        best_candidate_num = None
        min_score = float('inf')
        candidate_details_log = []

        logger.info(f"extraire_followers_spatial ({reseau_nom}): Recherche du meilleur candidat basé sur la proximité du mot-clé.")
        for kw_ann in keyword_annotations_list:
            logger.info(f"  - Analyse pour mot-clé: 	'{kw_ann['text']}' à y={kw_ann['avg_y']:.0f}, x={kw_ann['avg_x']:.0f}")
            for num_ann in number_annotations_list:
                y_diff = num_ann['avg_y'] - kw_ann['avg_y'] 
                x_diff = abs(num_ann['avg_x'] - kw_ann['avg_x'])
                
                logger.debug(f"    - Comparaison avec nombre: 	'{num_ann['text']}' (norm: {num_ann['normalized']}) à y={num_ann['avg_y']:.0f}. y_diff={y_diff:.2f}, x_diff={x_diff:.2f}")

                score = float('inf')
                # Cas 1: Nombre en dessous du mot-clé (Instagram, TikTok)
                if y_diff > -20 and y_diff < 100 and x_diff < 200: 
                    score = abs(y_diff) + x_diff * 0.5 
                    logger.debug(f"      Candidat (sous/proche Y): Score {score:.2f}")
                
                # Cas 2: Nombre avant ou après le mot-clé sur la même ligne (Twitter: "61 Followers")
                if abs(y_diff) < 40 and x_diff < 300: 
                    current_score_side = x_diff + abs(y_diff) * 0.8 
                    logger.debug(f"      Candidat (latéral): Score {current_score_side:.2f}")
                    if current_score_side < score: score = current_score_side
                
                candidate_details_log.append({'text': num_ann['text'], 'norm': num_ann['normalized'], 'score': score, 'kw': kw_ann['text']})
                if score < min_score:
                    min_score = score
                    best_candidate_num = num_ann['normalized']
                    logger.info(f"      NOUVEAU MEILLEUR CANDIDAT: 	'{num_ann['text']}' (norm: {num_ann['normalized']}), Score: {score:.2f} (avec mot-clé 	'{kw_ann['text']}')")

        if candidate_details_log:
            candidate_details_log.sort(key=lambda c: c['score'])
            logger.info(f"extraire_followers_spatial ({reseau_nom}): --- Top candidats (score ascendant) ---")
            for i, cand in enumerate(candidate_details_log[:5]):
                logger.info(f"    {i+1}. Num: 	'{cand['text']}' (norm: {cand['norm']}), Score: {cand['score']:.2f}, Mot-clé: 	'{cand['kw']}'")

        if best_candidate_num:
            logger.info(f"extraire_followers_spatial ({reseau_nom}): Candidat final sélectionné: {best_candidate_num}")
            return best_candidate_num
        else:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucun candidat follower trouvé après analyse spatiale.")
            if number_annotations_list and not keyword_annotations_list:
                 number_annotations_list.sort(key=lambda x: int(x.get("normalized", "0").replace(" ","")), reverse=True)
                 if number_annotations_list:
                     logger.warning(f"extraire_followers_spatial ({reseau_nom}) (Fallback ultime): Sélection du plus grand nombre: {number_annotations_list[0]['normalized']}")
                     return number_annotations_list[0]['normalized']
            return None

    except Exception as e:
        logger.error(f"extraire_followers_spatial ({reseau_nom}): ERREUR GLOBALE dans la fonction: {e}")
        logger.error(traceback.format_exc())
        return None

def identifier_reseau_social(texts_annotations):
    full_text_ocr = texts_annotations[0].description.lower() if texts_annotations else ""
    # Mots-clés plus spécifiques pour Twitter/X
    if "twitter" in full_text_ocr or " x " in full_text_ocr or "@x.com" in full_text_ocr or "profil / x" in full_text_ocr or "abonnés" in full_text_ocr and ("abonnements" in full_text_ocr or "suivre" in full_text_ocr):
        return "twitter"
    if "tiktok" in full_text_ocr or "j_aime" in full_text_ocr or ("followers" in full_text_ocr and "suivis" in full_text_ocr):
        return "tiktok"
    if "instagram" in full_text_ocr or ("followers" in full_text_ocr and "following" in full_text_ocr):
        return "instagram"
    if "threads" in full_text_ocr:
        return "threads"
    if "facebook" in full_text_ocr or ("j_aime" in full_text_ocr and "amis" in full_text_ocr):
        return "facebook"
    logger.warning(f"Réseau social non identifié. Texte OCR: {full_text_ocr[:300]}")
    return "inconnu"

def extraire_username(texts_annotations, reseau):
    full_text_ocr = texts_annotations[0].description if texts_annotations else ""
    username = None
    
    if reseau == "tiktok":
        match = re.search(r"@([a-zA-Z0-9_.]+)", full_text_ocr)
        if match:
            username = match.group(1)
            logger.info(f"Username TikTok trouvé par regex: @{username}")
            return username
    elif reseau == "instagram":
        # Chercher d'abord un @username clair
        match_at = re.search(r"@([a-zA-Z0-9_.]+)", full_text_ocr)
        if match_at:
            username = match_at.group(1)
            logger.info(f"Username Instagram trouvé par regex @: @{username}")
            return username
        # Sinon, chercher un nom plausible en haut de l'image
        for ann in texts_annotations[1:20]: 
            text = ann.description
            # Regex pour un nom d'utilisateur Instagram (peut contenir des points)
            if re.fullmatch(r"[a-z0-9_.]{3,30}", text.lower()): 
                vertices = ann.bounding_poly.vertices
                avg_y = sum(v.y for v in vertices) / 4
                if avg_y < 350: # Assez haut sur l'image
                    logger.info(f"Username Instagram potentiel (sans @, bien placé): {text}")
                    return text
            
    elif reseau == "twitter":
        # Pour Twitter, le @handle est souvent proéminent
        match = re.search(r"@([a-zA-Z0-9_]{1,15})", full_text_ocr) # Twitter handles: 1-15 chars, no points
        if match:
            username = match.group(1)
            logger.info(f"Username Twitter trouvé par regex: @{username}")
            return username
        # Fallback: chercher un nom affiché si le @ n'est pas là (moins fiable)
        # Souvent le nom est en plus gros au dessus du @handle
        # Cette logique est plus complexe et peut nécessiter une analyse spatiale aussi

    # Logique générique si les regex spécifiques échouent
    if not username:
        matches = re.findall(r"@([a-zA-Z0-9_.]+)", full_text_ocr)
        if matches:
            potential_usernames = [m for m in matches if len(m) > 2 and not m.lower() in ["gmail", "hotmail", "outlook"]]
            if potential_usernames:
                # Essayer de trouver un handle connu
                for pu in potential_usernames:
                    if pu.lower() in [h.lower() for h in KNOWN_HANDLES.get(reseau, [])] or \
                       any(pu.lower() in [h.lower() for h in h_list] for h_list in KNOWN_HANDLES.values()):
                        logger.info(f"Username générique trouvé (connu): @{pu}")
                        return pu
                username = potential_usernames[0]
                logger.info(f"Username générique trouvé par regex (premier de la liste): @{username}")
                return username
    
    if not username:
        logger.warning(f"Username non trouvé pour {reseau}. OCR: {full_text_ocr[:300]}")
    return username

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name_topic = "INCONNU"
    message_id_to_reply = update.message.message_id
    chat_id = update.message.chat_id
    photo_processed_successfully = False

    try:
        if not update.message.photo:
            logger.info("handle_photo: Le message ne contient pas de photo.")
            return

        if not update.message.reply_to_message or not update.message.reply_to_message.is_topic_message:
            logger.info("handle_photo: La photo n_est pas une réponse à un message de topic.")
            return

        topic_creator_message = update.message.reply_to_message
        if not topic_creator_message.forum_topic_created:
            logger.info("handle_photo: Le message auquel on répond n_est pas un message de création de topic.")
            return

        topic_name = topic_creator_message.forum_topic_created.name
        if not topic_name.startswith("SUIVI "):
            logger.info(f"handle_photo: Le nom du topic 	'{topic_name}' ne commence pas par 'SUIVI '. Traitement annulé.")
            return
        
        user_name_topic = topic_name.replace("SUIVI ", "").strip()
        logger.info(f"handle_photo: Traitement de l_image pour le topic: {topic_name} (Assistant: {user_name_topic})")

        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = io.BytesIO()
        await photo_file.download_to_memory(photo_bytes)
        photo_bytes.seek(0)

        image_pil = Image.open(photo_bytes)
        width, height = image_pil.size
        cropped_image = image_pil.crop((0, 0, width, int(height * 0.55)))
        
        img_byte_arr = io.BytesIO()
        cropped_image.save(img_byte_arr, format='PNG')
        content = img_byte_arr.getvalue()
        
        image_vision = vision.Image(content=content)
        response = vision_client.text_detection(image=image_vision)
        texts_annotations = response.text_annotations

        if response.error.message:
            raise Exception(f"Erreur de l_API Google Vision: {response.error.message}")

        if not texts_annotations:
            logger.warning("Aucun texte détecté par Google Vision AI.")
            raise ValueError("Aucun texte détecté par Google Vision AI.")

        reseau = identifier_reseau_social(texts_annotations)
        logger.info(f"handle_photo: Réseau identifié: {reseau}")

        username = extraire_username(texts_annotations, reseau)
        if username:
            username = corriger_username(username, reseau)
            logger.info(f"🕵️ Username final : 	'{username}' (réseau : {reseau})")
        else:
            logger.warning(f"Impossible d_extraire le nom d_utilisateur pour le réseau {reseau}.")
            raise ValueError(f"Nom d_utilisateur non trouvé pour {reseau}")

        mots_cles_followers = {
            "tiktok": ["followers", "abonnés", "fans", "j_aime"],
            "instagram": ["followers", "abonnés"],
            "twitter": ["followers", "abonnés", "suivre"], 
            "threads": ["followers", "abonnés"],
            "facebook": ["amis", "j_aime", "personnes suivent ça"]
        }
        abonnés = extraire_followers_spatial(texts_annotations, mots_cles_followers.get(reseau, ["followers", "abonnés"]), reseau)

        if abonnés:
            logger.info(f"📊 Followers extraits: {abonnés}")
        else:
            logger.warning(f"Impossible d_extraire le nombre de followers pour {username} sur {reseau}.")
            raise ValueError(f"Nombre de followers non trouvé pour {username} sur {reseau}")

        now = datetime.datetime.now().strftime("%d/%m/%Y")
        row = [now, user_name_topic.upper(), reseau, f"@{username}", abonnés]
        sheet.append_row(row)
        logger.info(f"Données ajoutées à Google Sheets: {row}")
        photo_processed_successfully = True

    except ValueError as ve:
        logger.warning(f"ValueError dans handle_photo: {ve}")
    except Exception as e:
        logger.error("❌ Erreur globale dans handle_photo")
        logger.error(traceback.format_exc())
    finally:
        if user_name_topic != "INCONNU":
            today_date_str = datetime.datetime.now().strftime("%d/%m/%Y")
            va_name = user_name_topic.upper() if user_name_topic else "INCONNU"
            
            general_topic_message_id = None
            GENERAL_TOPIC_THREAD_ID = os.getenv("TELEGRAM_GENERAL_TOPIC_THREAD_ID")
            if GENERAL_TOPIC_THREAD_ID:
                try:
                    general_topic_message_id = int(GENERAL_TOPIC_THREAD_ID)
                    logger.info(f"Utilisation du GENERAL_TOPIC_THREAD_ID: {general_topic_message_id}")
                except ValueError:
                    logger.error(f"TELEGRAM_GENERAL_TOPIC_THREAD_ID n_est pas un entier valide: {GENERAL_TOPIC_THREAD_ID}")
            else:
                logger.warning("TELEGRAM_GENERAL_TOPIC_THREAD_ID non défini. Le message de statut sera envoyé au chat principal.")

            if photo_processed_successfully:
                status_message = f"🤖 {today_date_str} - {va_name} - ✅ 1 compte détecté et ajouté ✅"
            else:
                status_message = f"🤖 {today_date_str} - {va_name} - ❌ Analyse OCR impossible ❌"
            
            try:
                await bot.send_message(
                    chat_id=GROUP_ID, 
                    text=status_message,
                    message_thread_id=general_topic_message_id
                )
                logger.info(f"Message de statut envoyé au sujet General (ou chat principal): {status_message}")
            except Exception as e_send_status:
                logger.error(f"Erreur lors de l_envoi du message de statut au sujet General: {e_send_status}")
                logger.error(traceback.format_exc())

async def webhook_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug(f"Webhook received update: {update.to_json()[:500]}...") # Log tronqué
    if update.message and update.message.photo and \
       update.message.reply_to_message and \
       update.message.reply_to_message.is_topic_message and \
       update.message.reply_to_message.forum_topic_created and \
       update.message.reply_to_message.forum_topic_created.name.startswith("SUIVI "):
        await handle_photo(update, context)
    else:
        logger.info("Webhook_handler: Message non pertinent pour handle_photo.")

async def startup_webhook(application: Application):
    logger.info("Application startup for webhook...")
    if not RAILWAY_PUBLIC_URL:
        logger.error("RAILWAY_PUBLIC_URL n_est pas défini. Impossible de configurer le webhook.")
        return

    webhook_url = RAILWAY_PUBLIC_URL.rstrip('/') + "/webhook"
    logger.info(f"Setting webhook to: {webhook_url}")
    await application.bot.set_webhook(webhook_url, allowed_updates=Update.ALL_TYPES)
    logger.info("Webhook set.")

# Variable globale pour l_application PTB, utilisée par Uvicorn
ptb_app = None

def main() -> Application:
    global ptb_app
    if not TOKEN:
        logger.error("La variable d_environnement TELEGRAM_BOT_TOKEN n_est pas définie.")
        exit()
    if not GROUP_ID:
        logger.error("La variable d_environnement TELEGRAM_GROUP_ID n_est pas définie.")
        exit()
    if not SPREADSHEET_ID:
        logger.error("La variable d_environnement SPREADSHEET_ID n_est pas définie.")
        exit()

    # Utiliser post_init pour configurer le webhook après que la boucle d_événements soit active
    application_builder = Application.builder().token(TOKEN)
    if RAILWAY_PUBLIC_URL: # Configurer le webhook seulement si l_URL est disponible (mode webhook)
        application_builder.post_init(startup_webhook)
    
    ptb_app = application_builder.build()

    ptb_app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.GROUPS, webhook_handler))
    
    logger.info("Application Telegram initialisée.")
    return ptb_app.asgi

# Exécuter main() pour initialiser ptb_app au chargement du module
# Uvicorn cherchera `main:ptb_app` (ou le nom que vous spécifiez dans Procfile, ex: `main:application`)
app = main() # `app` est maintenant l_objet ASGI que Uvicorn peut servir

if __name__ == "__main__":
    # Cette section est généralement pour le polling local, non utilisé sur Railway avec Uvicorn
    logger.info("Lancement du bot en mode polling (pour test local uniquement)...")
    if ptb_app:
        ptb_app.run_polling()
    else:
        logger.error("L_application PTB n_a pas été initialisée.")

