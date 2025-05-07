import json
import io
import re
import datetime
import logging
import os
import traceback
from difflib import get_close_matches

from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from PIL import Image, ImageOps
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
    nombre_str_clean_pour_km = re.sub(r"[\s.,]", "", nombre_str_clean_pour_km)
    
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
        logger.warning(f"normaliser_nombre_followers: ValueError lors de la conversion de 	'{nombre_str_test}' (nettoyé en 	'{nombre_str_clean_pour_float}' ou 	'{cleaned_for_log}'). Erreur: {e_norm}")
        return None
    return valeur

def identifier_reseau_social(text_ocr: str) -> str:
    text_ocr_lower = text_ocr.lower()
    logger.info(f"identifier_reseau_social: Début identification. Texte OCR (premiers 500 chars): {text_ocr_lower[:500]}")

    # Instagram
    instagram_keywords = [
        "instagram", "publications", "followers", "suivi(e)s", "suivis", "suivies", 
        "profil", "modifier profil", "partager le profil", "story", "stories", "reels", "explorer",
        "tableau de bord", "voir le tableau de bord", "message", "messages"
    ]
    instagram_score = 0
    instagram_specific_combo = False
    for keyword in instagram_keywords:
        if keyword in text_ocr_lower:
            instagram_score += 1
            logger.info(f"identifier_reseau_social: Mot-clé Instagram trouvé: \t'{keyword}'")
    
    if "publications" in text_ocr_lower and "followers" in text_ocr_lower and "suivi" in text_ocr_lower: # "suivi" pour couvrir suivi(e)s
        instagram_specific_combo = True
        logger.info("identifier_reseau_social: Combinaison spécifique Instagram (publications, followers, suivi) trouvée.")

    logger.info(f"identifier_reseau_social: Score Instagram final: {instagram_score}")
    if instagram_specific_combo and instagram_score >= 2:
        logger.info("identifier_reseau_social: Instagram identifié par combinaison clé et score suffisant.")
        return "instagram"
    if "instagram" in text_ocr_lower and instagram_score >= 1:
        logger.info("identifier_reseau_social: Instagram identifié par mot-clé 'instagram' et au moins un autre indicateur.")
        return "instagram"
    if instagram_score >= 3:
        logger.info(f"identifier_reseau_social: Instagram identifié par score élevé ({instagram_score}).")
        return "instagram"

    # Twitter / X
    twitter_keywords = [
        "twitter", " x ", "accueil", "explorer", "notifications", "messages", 
        "abonnements", "abonnés", "profil", "tweets", "retweets", "posts", "communautés",
        "vérifié", "éditer le profil", "pour vous", "suivis", "listes"
    ]
    twitter_score = 0
    twitter_specific_combo = False
    for keyword in twitter_keywords:
        if keyword in text_ocr_lower:
            twitter_score += 1
            logger.info(f"identifier_reseau_social: Mot-clé Twitter/X trouvé: \t'{keyword}'")
    
    if "abonnements" in text_ocr_lower and "abonnés" in text_ocr_lower:
        twitter_specific_combo = True
        logger.info("identifier_reseau_social: Combinaison spécifique Twitter/X (abonnements, abonnés) trouvée.")

    logger.info(f"identifier_reseau_social: Score Twitter/X final: {twitter_score}")
    if twitter_specific_combo and twitter_score >= 2:
        logger.info("identifier_reseau_social: Twitter/X identifié par combinaison clé et score suffisant.")
        return "twitter"
    if "twitter" in text_ocr_lower and twitter_score >= 1:
         logger.info("identifier_reseau_social: Twitter/X identifié par mot-clé 'twitter' et au moins un autre indicateur.")
         return "twitter"
    if " x " in text_ocr_lower and twitter_score >= 2:
         logger.info("identifier_reseau_social: Twitter/X identifié par mot-clé ' x ' et au moins un autre indicateur.")
         return "twitter"
    if twitter_score >= 3:
        logger.info(f"identifier_reseau_social: Twitter/X identifié par score élevé ({twitter_score}).")
        return "twitter"

    # TikTok
    tiktok_keywords = ["tiktok", "pour toi", "following", "followers", "j_aime", "profil", "messages", "découvrir", "amis", "boîte de réception"]
    tiktok_score = 0
    for keyword in tiktok_keywords:
        if keyword in text_ocr_lower:
            tiktok_score += 1
            logger.info(f"identifier_reseau_social: Mot-clé TikTok trouvé: \t'{keyword}'")

    logger.info(f"identifier_reseau_social: Score TikTok final: {tiktok_score}")
    if "tiktok" in text_ocr_lower and tiktok_score >= 1:
        logger.info("identifier_reseau_social: TikTok identifié par mot-clé 'tiktok' et au moins un autre indicateur.")
        return "tiktok"
    if tiktok_score >= 3: 
        logger.info(f"identifier_reseau_social: TikTok identifié par score élevé ({tiktok_score}).")
        return "tiktok"
        
    # Facebook
    facebook_keywords = ["facebook", "fil d_actualité", "stories", "reels", "profil", "amis", "j_aime", "commenter", "partager"]
    facebook_score = 0
    for keyword in facebook_keywords:
        if keyword in text_ocr_lower:
            facebook_score += 1
            logger.info(f"identifier_reseau_social: Mot-clé Facebook trouvé: \t'{keyword}'")
            
    logger.info(f"identifier_reseau_social: Score Facebook final: {facebook_score}")
    if "facebook" in text_ocr_lower and facebook_score >= 2:
        logger.info("identifier_reseau_social: Facebook identifié par mot-clé 'facebook' et au moins un autre indicateur.")
        return "facebook"
    if facebook_score >= 3:
        logger.info(f"identifier_reseau_social: Facebook identifié par score élevé ({facebook_score}).")
        return "facebook"

    logger.warning(f"identifier_reseau_social: Réseau non identifié après toutes les vérifications. Scores -> Instagram: {instagram_score}, Twitter/X: {twitter_score}, TikTok: {tiktok_score}, Facebook: {facebook_score}. OCR (500 chars): {text_ocr_lower[:500]}")
    return "inconnu"

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
                best_merged_ann = current_num_ann
                current_merged_indices = {i}

                for j in range(i + 1, len(temp_merged_list)):
                    if j in processed_indices:
                        continue
                    
                    next_num_ann_to_try = temp_merged_list[j]
                    
                    y_diff_merge = abs(best_merged_ann['avg_y'] - next_num_ann_to_try['avg_y'])
                    x_gap_merge = next_num_ann_to_try['min_x'] - best_merged_ann['max_x']

                    if y_diff_merge < 20 and x_gap_merge >= -10 and x_gap_merge < 50:
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
                            current_merged_indices.add(j)
                    else:
                        pass 
            
                merged_numbers_final.append(best_merged_ann)
                processed_indices.update(current_merged_indices)

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
                if y_diff > -20 and y_diff < 100 and x_diff < 200: 
                    score = abs(y_diff) + x_diff * 0.5 
                    logger.debug(f"      Candidat (sous/proche Y): Score {score:.2f}")
                
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
            candidate_details_log.sort(key=lambda x: x['score'])
            logger.info(f"extraire_followers_spatial ({reseau_nom}): --- Top 3 candidats (score, norm, kw) ---")
            for cand_log in candidate_details_log[:3]:
                logger.info(f"    Score: {cand_log['score']:.2f}, Num: 	'{cand_log['norm']}' (	'{cand_log['text']}	'), Kw: 	'{cand_log['kw']}	'")

        if best_candidate_num:
            logger.info(f"extraire_followers_spatial ({reseau_nom}): MEILLEUR CANDIDAT FINAL: {best_candidate_num} avec score {min_score:.2f}")
            return best_candidate_num
        else:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucun candidat de followers trouvé après analyse de proximité.")
            return None

    except Exception as e_spatial:
        logger.error(f"extraire_followers_spatial ({reseau_nom}): ERREUR MAJEURE dans la fonction: {e_spatial}")
        logger.error(traceback.format_exc())
        return None

def extraire_username(text_annotations, reseau: str, full_text_ocr: str) -> str | None:
    logger.info(f"extraire_username: Début extraction pour réseau: {reseau}")
    if not text_annotations:
        logger.warning("extraire_username: Aucune annotation de texte fournie.")
        return None

    username_found = None
    full_text_lower = full_text_ocr.lower()

    if reseau == "instagram":
        # Essayer de trouver le @username en haut de l'image, souvent le plus grand texte
        # Ou chercher des motifs comme "modifier profil" puis le texte au-dessus
        # Ou le premier texte significatif après "instagram"
        # Tentative 1: Chercher un @username proéminent
        for ann in text_annotations[1:]:
            if ann.description.startswith("@") and len(ann.description) > 1:
                username_found = ann.description
                logger.info(f"extraire_username (Instagram): Trouvé via @: {username_found}")
                return corriger_username(username_found, reseau)
        
        # Tentative 2: Chercher près de "Publications", "Followers", "Suivi(e)s"
        # Souvent le nom est au-dessus de ces indicateurs
        # On cherche un texte qui n'est pas un de ces mots-clés, et qui est au-dessus
        y_ref = float('inf')
        potential_usernames_above_stats = []
        for ann in text_annotations[1:]:
            if any(kw in ann.description.lower() for kw in ["publications", "followers", "suivi(e)s"]):
                y_ref = min(y_ref, ann.bounding_poly.vertices[0].y)
        
        if y_ref != float('inf'):
            for ann in text_annotations[1:]:
                ann_text_lower = ann.description.lower()
                if ann.bounding_poly.vertices[3].y < y_ref - 5 and \
                   not any(kw in ann_text_lower for kw in ["publications", "followers", "suivi(e)s", "profil", "modifier", "message", "story", "reels"]) and \
                   len(ann.description) > 2 and not ann.description.isdigit() and "instagram" not in ann_text_lower:
                    potential_usernames_above_stats.append(ann.description)
            
            if potential_usernames_above_stats:
                # Prendre le plus probable (par ex. le plus long, ou celui avec le moins de chiffres)
                potential_usernames_above_stats.sort(key=len, reverse=True)
                username_found = potential_usernames_above_stats[0]
                logger.info(f"extraire_username (Instagram): Trouvé au-dessus des stats: {username_found}")
                return corriger_username(username_found, reseau)

        # Tentative 3: Utiliser KNOWN_HANDLES si le nom de fichier correspond
        # Cette logique est maintenant dans handle_photo, avant l'appel à extraire_username

    elif reseau == "twitter":
        # Chercher le @username, souvent sous le nom complet
        # Le nom complet est souvent plus grand, le @username commence par @
        for ann in text_annotations[1:]:
            if ann.description.startswith("@") and len(ann.description) > 1:
                username_found = ann.description
                logger.info(f"extraire_username (Twitter): Trouvé via @: {username_found}")
                return corriger_username(username_found, reseau)
        
        # Si pas de @, chercher un texte plausible près de "Abonnements" / "Abonnés"
        # souvent le nom d'affichage est au-dessus de ces termes.
        y_ref_twitter = float('inf')
        potential_usernames_twitter = []
        for ann in text_annotations[1:]:
            if any(kw in ann.description.lower() for kw in ["abonnements", "abonnés"]):
                y_ref_twitter = min(y_ref_twitter, ann.bounding_poly.vertices[0].y)
        
        if y_ref_twitter != float('inf'):
            for ann in text_annotations[1:]:
                ann_text_lower = ann.description.lower()
                if ann.bounding_poly.vertices[3].y < y_ref_twitter - 5 and \
                   not any(kw in ann_text_lower for kw in ["abonnements", "abonnés", "profil", "éditer", "notifications", "messages"]) and \
                   len(ann.description) > 2 and not ann.description.isdigit() and "twitter" not in ann_text_lower and " x " not in ann_text_lower:
                    potential_usernames_twitter.append(ann.description)
            
            if potential_usernames_twitter:
                potential_usernames_twitter.sort(key=len, reverse=True)
                username_found = potential_usernames_twitter[0]
                logger.info(f"extraire_username (Twitter): Trouvé au-dessus des stats (sans @): {username_found}")
                return corriger_username(username_found, reseau)

    elif reseau == "tiktok":
        # Sur TikTok, le @username est souvent proéminent
        for ann in text_annotations[1:]:
            if ann.description.startswith("@") and len(ann.description) > 1:
                username_found = ann.description
                logger.info(f"extraire_username (TikTok): Trouvé via @: {username_found}")
                return corriger_username(username_found, reseau)
        # Fallback: chercher un texte qui ressemble à un nom d'utilisateur près de "Profil"
        # ou le texte le plus grand en haut de l'écran.
        # Cette partie peut être affinée si nécessaire.

    if not username_found:
        logger.warning(f"extraire_username: Nom d_utilisateur non trouvé pour {reseau} avec les méthodes actuelles.")
    return username_found

def get_text_from_image_vision(image_bytes: bytes) -> tuple[str | None, list | None]:
    try:
        image = vision.Image(content=image_bytes)
        response = vision_client.text_detection(image=image)
        texts = response.text_annotations

        if response.error.message:
            logger.error(f"Erreur de l_API Google Vision: {response.error.message}")
            return None, None
        
        if texts:
            logger.info(f"get_text_from_image_vision: Texte extrait avec succès. Premier élément (texte complet): {texts[0].description[:100]}...")
            return texts[0].description, texts
        else:
            logger.warning("get_text_from_image_vision: Aucun texte détecté par Google Vision.")
            return None, None
    except Exception as e:
        logger.error(f"Erreur lors de l_extraction du texte avec Google Vision: {e}")
        logger.error(traceback.format_exc())
        return None, None

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo:
        logger.info("handle_photo: Message sans photo reçu, ignoré.")
        return

    user = update.message.from_user
    chat_id = update.message.chat_id
    message_id = update.message.message_id
    thread_id = update.message.message_thread_id
    
    general_topic_thread_id_str = os.getenv("TELEGRAM_GENERAL_TOPIC_THREAD_ID")
    general_topic_thread_id = int(general_topic_thread_id_str) if general_topic_thread_id_str and general_topic_thread_id_str.isdigit() else None

    logger.info(f"handle_photo: Photo reçue de {user.username} (ID: {user.id}) dans le chat {chat_id} (Thread: {thread_id}).")

    # Vérifier si le message provient d'un sujet "SUIVI..."
    topic_name = ""
    if thread_id:
        try:
            # Pour obtenir le nom du sujet, il faudrait une méthode pour lister les sujets ou une base de données.
            # Pour l'instant, on se base sur une convention de nommage si on peut l'obtenir.
            # Cette partie est complexe sans accès direct aux noms des sujets.
            # On va supposer que si c'est un thread, c'est un sujet de suivi pour l'instant.
            # Idéalement, on filtrerait sur le nom du sujet commençant par "SUIVI".
            # Pour l'instant, on traite toutes les images dans les sujets.
            pass # On continue si c'est un thread
        except Exception as e_topic_name:
            logger.warning(f"handle_photo: Impossible de déterminer le nom du sujet pour le thread_id {thread_id}: {e_topic_name}")
            # On continue quand même, mais le nom du modèle sera vide

    file_id = update.message.photo[-1].file_id
    try:
        new_file = await context.bot.get_file(file_id)
        file_path = new_file.file_path
        logger.info(f"handle_photo: Informations du fichier obtenues: {file_path}")

        # Télécharger l_image en mémoire
        img_byte_array = io.BytesIO()
        await new_file.download_to_memory(img_byte_array)
        img_byte_array.seek(0)
        image_bytes_content = img_byte_array.read()
        logger.info(f"handle_photo: Image téléchargée en mémoire ({len(image_bytes_content)} bytes).")

        # Recadrer l_image (55% supérieur)
        try:
            img = Image.open(io.BytesIO(image_bytes_content))
            width, height = img.size
            crop_height = int(height * 0.55)
            cropped_img = img.crop((0, 0, width, crop_height))
            
            # Convertir l_image recadrée en bytes pour Vision AI
            cropped_img_byte_array = io.BytesIO()
            cropped_img.save(cropped_img_byte_array, format=img.format if img.format else 'PNG') # S'assurer du format
            image_bytes_for_vision = cropped_img_byte_array.getvalue()
            logger.info(f"handle_photo: Image recadrée à 55% ({len(image_bytes_for_vision)} bytes) et prête pour Vision AI.")
        except Exception as e_crop:
            logger.error(f"handle_photo: Erreur lors du recadrage de l_image: {e_crop}. Utilisation de l_image originale.")
            logger.error(traceback.format_exc())
            image_bytes_for_vision = image_bytes_content # Fallback à l'image originale

        full_text_ocr, text_annotations = get_text_from_image_vision(image_bytes_for_vision)

        if not full_text_ocr or not text_annotations:
            logger.warning("handle_photo: OCR n_a retourné aucun texte. Abandon.")
            if general_topic_thread_id:
                await context.bot.send_message(chat_id=chat_id, message_thread_id=general_topic_thread_id, text=f"🤖 {datetime.date.today().strftime('%d/%m/%Y')} - {user.username or user.first_name} - ❌ OCR n_a rien détecté sur l_image. ❌")
            return

        reseau_social = identifier_reseau_social(full_text_ocr)
        logger.info(f"handle_photo: Réseau identifié: {reseau_social}")

        nom_modele = ""
        # Essayer d_extraire le nom du modèle à partir du nom du sujet (si possible et pertinent)
        # Cette partie dépend de la capacité à obtenir le nom du sujet.
        # Pour l'instant, on se base sur KNOWN_HANDLES si le nom de fichier correspond.

        # Logique pour obtenir le nom du modèle à partir de KNOWN_HANDLES
        # Cela suppose que le nom du fichier de la photo (si disponible et pertinent) ou une autre info
        # peut être mappée à un nom de modèle via KNOWN_HANDLES.
        # Pour l'instant, on va essayer de trouver un @username dans l'OCR et le chercher dans KNOWN_HANDLES
        
        username_ocr = extraire_username(text_annotations, reseau_social, full_text_ocr)
        if username_ocr:
            username_ocr_corrected = corriger_username(username_ocr, reseau_social)
            logger.info(f"handle_photo: Username extrait de l_OCR: 	'{username_ocr_corrected}	' (original: 	'{username_ocr}	')")
            # Chercher si ce username_ocr est une clé dans KNOWN_HANDLES
            if username_ocr_corrected in KNOWN_HANDLES:
                nom_modele = KNOWN_HANDLES[username_ocr_corrected]
                logger.info(f"handle_photo: Nom de modèle trouvé via KNOWN_HANDLES pour 	'{username_ocr_corrected}	': {nom_modele}")
            else:
                # Essayer une correspondance approchante
                match = get_close_matches(username_ocr_corrected, KNOWN_HANDLES.keys(), n=1, cutoff=0.8)
                if match:
                    nom_modele = KNOWN_HANDLES[match[0]]
                    logger.info(f"handle_photo: Nom de modèle trouvé par correspondance approchante pour 	'{username_ocr_corrected}	' (match: 	'{match[0]}	'): {nom_modele}")
                else:
                    nom_modele = username_ocr_corrected # Fallback au username OCR si non trouvé
                    logger.info(f"handle_photo: Nom de modèle non trouvé dans KNOWN_HANDLES pour 	'{username_ocr_corrected}	'. Utilisation de l_username OCR comme nom de modèle.")
        else:
            logger.warning("handle_photo: Aucun nom d_utilisateur n_a pu être extrait de l_OCR.")
            nom_modele = "Inconnu" # Fallback si aucun username n'est extrait

        followers = None
        if reseau_social == "instagram":
            followers = extraire_followers_spatial(text_annotations, ["followers", "follower"], "instagram")
        elif reseau_social == "twitter":
            followers = extraire_followers_spatial(text_annotations, ["abonnés", "abonné"], "twitter")
        elif reseau_social == "tiktok":
            followers = extraire_followers_spatial(text_annotations, ["followers", "abonnés", "abonné"], "tiktok")
        # Pas de gestion spécifique pour Facebook followers pour l'instant
        
        if followers:
            logger.info(f"handle_photo: Followers extraits pour {reseau_social} - {nom_modele}: {followers}")
            # Enregistrer dans Google Sheets
            try:
                row = [datetime.date.today().strftime("%d/%m/%Y"), user.username or user.first_name, reseau_social, nom_modele, followers]
                sheet.append_row(row)
                logger.info(f"handle_photo: Données enregistrées dans Google Sheets: {row}")
                if general_topic_thread_id:
                    await context.bot.send_message(chat_id=chat_id, message_thread_id=general_topic_thread_id, text=f"🤖 {row[0]} - {row[1]} - ✅ {row[2]}/{row[3]} : {row[4]} followers enregistrés.")
            except Exception as e_gsheet:
                logger.error(f"handle_photo: Erreur lors de l_enregistrement dans Google Sheets: {e_gsheet}")
                logger.error(traceback.format_exc())
                if general_topic_thread_id:
                    await context.bot.send_message(chat_id=chat_id, message_thread_id=general_topic_thread_id, text=f"🤖 {datetime.date.today().strftime('%d/%m/%Y')} - {user.username or user.first_name} - ⚠️ Erreur lors de l_enregistrement Google Sheets pour {reseau_social}/{nom_modele}.")
        else:
            logger.warning(f"handle_photo: Analyse OCR impossible ou followers non trouvés pour {reseau_social} - {nom_modele}.")
            if general_topic_thread_id:
                await context.bot.send_message(chat_id=chat_id, message_thread_id=general_topic_thread_id, text=f"🤖 {datetime.date.today().strftime('%d/%m/%Y')} - {user.username or user.first_name} - ❌ Analyse OCR impossible pour {reseau_social}/{nom_modele}. ❌")

    except Exception as e:
        logger.error(f"Erreur inattendue dans handle_photo: {e}")
        logger.error(traceback.format_exc())
        if general_topic_thread_id:
            try:
                await context.bot.send_message(chat_id=chat_id, message_thread_id=general_topic_thread_id, text=f"🤖 {datetime.date.today().strftime('%d/%m/%Y')} - {user.username or user.first_name} - ❗️ Erreur grave dans le traitement de l_image.")
            except Exception as e_send_error:
                logger.error(f"Impossible d_envoyer le message d_erreur grave: {e_send_error}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    logger.info(f"Message texte reçu de {update.message.from_user.username}: {update.message.text[:50]}... Ignoré car ce bot ne traite que les photos.")

def main() -> Application:
    ptb_app = Application.builder().token(TOKEN).build()
    ptb_app.add_handler(MessageHandler(filters.PHOTO & (~filters.COMMAND), handle_photo))
    ptb_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    logger.info("Application Telegram initialisée avec les handlers photo et texte.")
    return ptb_app

if __name__ == "__main__":
    application = main()
    logger.info("Lancement du bot en mode polling...")
    application.run_polling()

