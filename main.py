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
TELEGRAM_GENERAL_TOPIC_THREAD_ID = os.getenv("TELEGRAM_GENERAL_TOPIC_THREAD_ID")


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

KNOWN_HANDLES = {}
try:
    with open("known_handles.json", "r", encoding="utf-8") as f:
        KNOWN_HANDLES = json.load(f)
    logger.info("Fichier known_handles.json chargé.")
except FileNotFoundError:
    logger.warning("Le fichier known_handles.json n_a pas été trouvé. Le bot fonctionnera sans.")
except json.JSONDecodeError:
    logger.error("Erreur lors du décodage de known_handles.json. Le fichier est peut-être corrompu.")
except Exception as e:
    logger.error(f"Erreur inattendue lors du chargement de known_handles.json: {e}")

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
    if "," in nombre_str_clean_pour_float and "." in nombre_str_clean_pour_float:
        if nombre_str_clean_pour_float.rfind(".") < nombre_str_clean_pour_float.rfind(","):
            nombre_str_clean_pour_float = nombre_str_clean_pour_float.replace(".","")
            nombre_str_clean_pour_float = nombre_str_clean_pour_float.replace(",",".")
        else: 
            nombre_str_clean_pour_float = nombre_str_clean_pour_float.replace(",","")
    else:
        nombre_str_clean_pour_float = nombre_str_clean_pour_float.replace(",",".")
    
    parts = nombre_str_clean_pour_float.split(".")
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

    instagram_keywords = [
        "instagram", "publications", "followers", "suivi(e)s", "suivis", "suivies", 
        "profil", "modifier profil", "partager le profil", "story", "stories", "reels", "explorer",
        "tableau de bord", "voir le tableau de bord", "message", "messages"
    ]
    instagram_score = 0
    for keyword in instagram_keywords:
        if keyword in text_ocr_lower:
            instagram_score += 1
            logger.info(f"identifier_reseau_social: Mot-clé Instagram trouvé: 	'{keyword}'")
    
    if "publications" in text_ocr_lower and "followers" in text_ocr_lower and "suivi" in text_ocr_lower:
        logger.info("identifier_reseau_social: Combinaison spécifique Instagram (publications, followers, suivi) trouvée.")
        return "instagram"
    if "instagram" in text_ocr_lower and instagram_score >= 1:
        logger.info("identifier_reseau_social: Instagram identifié par mot-clé 'instagram' et au moins un autre indicateur.")
        return "instagram"
    if instagram_score >= 3:
        logger.info(f"identifier_reseau_social: Instagram identifié par score élevé ({instagram_score}).")
        return "instagram"

    twitter_keywords = [
        "twitter", " x ", "accueil", "explorer", "notifications", "messages", 
        "abonnements", "abonnés", "profil", "tweets", "retweets", "posts", "communautés",
        "vérifié", "éditer le profil", "pour vous", "suivis", "listes"
    ]
    twitter_score = 0
    for keyword in twitter_keywords:
        if keyword in text_ocr_lower:
            twitter_score += 1
            logger.info(f"identifier_reseau_social: Mot-clé Twitter/X trouvé: 	'{keyword}'")
    
    if "abonnements" in text_ocr_lower and "abonnés" in text_ocr_lower:
        logger.info("identifier_reseau_social: Combinaison spécifique Twitter/X (abonnements, abonnés) trouvée.")
        return "twitter"
    if ("twitter" in text_ocr_lower or " x " in text_ocr_lower) and twitter_score >= 1:
         logger.info("identifier_reseau_social: Twitter/X identifié par mot-clé 'twitter' ou ' x ' et au moins un autre indicateur.")
         return "twitter"
    if twitter_score >= 3:
        logger.info(f"identifier_reseau_social: Twitter/X identifié par score élevé ({twitter_score}).")
        return "twitter"

    tiktok_keywords = ["tiktok", "pour toi", "following", "followers", "j_aime", "profil", "messages", "découvrir", "amis", "boîte de réception"]
    tiktok_score = 0
    for keyword in tiktok_keywords:
        if keyword in text_ocr_lower:
            tiktok_score += 1
            logger.info(f"identifier_reseau_social: Mot-clé TikTok trouvé: 	'{keyword}'")

    if "tiktok" in text_ocr_lower and tiktok_score >= 1:
        logger.info("identifier_reseau_social: TikTok identifié par mot-clé 'tiktok' et au moins un autre indicateur.")
        return "tiktok"
    if tiktok_score >= 3: 
        logger.info(f"identifier_reseau_social: TikTok identifié par score élevé ({tiktok_score}).")
        return "tiktok"
        
    facebook_keywords = ["facebook", "fil d_actualité", "stories", "reels", "profil", "amis", "j_aime", "commenter", "partager"]
    facebook_score = 0
    for keyword in facebook_keywords:
        if keyword in text_ocr_lower:
            facebook_score += 1
            logger.info(f"identifier_reseau_social: Mot-clé Facebook trouvé: 	'{keyword}'")
            
    if "facebook" in text_ocr_lower and facebook_score >= 2:
        logger.info("identifier_reseau_social: Facebook identifié par mot-clé 'facebook' et au moins un autre indicateur.")
        return "facebook"
    if facebook_score >= 3:
        logger.info(f"identifier_reseau_social: Facebook identifié par score élevé ({facebook_score}).")
        return "facebook"

    logger.warning(f"identifier_reseau_social: Réseau non identifié. Scores -> IG:{instagram_score}, TW:{twitter_score}, TK:{tiktok_score}, FB:{facebook_score}. OCR: {text_ocr_lower[:200]}")
    return "inconnu"

def extraire_followers_spatial(text_annotations, mots_cles_specifiques, reseau_nom="inconnu") -> str | None:
    try:
        logger.info(f"extraire_followers_spatial ({reseau_nom}): --- Début --- N_ann: {len(text_annotations) if text_annotations else 0}")
        keyword_annotations_list = []
        number_annotations_list = []

        if not text_annotations:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucune annotation.")
            return None
        
        for i, annotation in enumerate(text_annotations[1:]): 
            try:
                if not hasattr(annotation, 'description') or not hasattr(annotation, 'bounding_poly'):
                    continue
                text_desc = annotation.description 
                text_lower = text_desc.lower().strip()
                if not hasattr(annotation.bounding_poly, 'vertices') or len(annotation.bounding_poly.vertices) < 4:
                    continue
                
                vertices = annotation.bounding_poly.vertices
                avg_y = sum(v.y for v in vertices) / 4
                avg_x = sum(v.x for v in vertices) / 4
                min_x, max_x = min(v.x for v in vertices), max(v.x for v in vertices)
                min_y, max_y = min(v.y for v in vertices), max(v.y for v in vertices)

                logger.debug(f"extraire_followers_spatial ({reseau_nom}): Ann {i}: '{text_desc}' (y:{avg_y:.0f}, x:{avg_x:.0f})")

                if any(keyword.lower() in text_lower for keyword in mots_cles_specifiques):
                    keyword_annotations_list.append({
                        "text": text_lower, "avg_y": avg_y, "avg_x": avg_x, 
                        "min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y, 
                        "annotation": annotation
                    })
                    logger.info(f"extraire_followers_spatial ({reseau_nom}): MOT-CLÉ: '{text_lower}' y:{avg_y:.0f}, x:{avg_x:.0f}")
                
                if re.search(r"\d", text_desc) and not re.fullmatch(r"\d{1,2}:\d{2}", text_desc):
                    if re.match(r"^[\d.,\s]+[kKm]?$", text_desc.replace(" ", ""), re.IGNORECASE): 
                        nombre_normalise_test = normaliser_nombre_followers(text_desc) 
                        if nombre_normalise_test:
                            number_annotations_list.append({
                                "text": text_desc, "normalized": nombre_normalise_test, 
                                "avg_y": avg_y, "avg_x": avg_x, "min_x": min_x, "max_x": max_x, 
                                "min_y": min_y, "max_y": max_y, "annotation": annotation
                            })
                            logger.info(f"extraire_followers_spatial ({reseau_nom}): NOMBRE: '{text_desc}' (norm: {nombre_normalise_test}) y:{avg_y:.0f}, x:{avg_x:.0f}")
                        else:
                            logger.debug(f"extraire_followers_spatial ({reseau_nom}): '{text_desc}' non normalisable.")
                    else:
                        logger.debug(f"extraire_followers_spatial ({reseau_nom}): '{text_desc}' non format num k/m.")
            except Exception as e_loop_ann:
                desc_log = annotation.description if hasattr(annotation, 'description') else 'N/A'
                logger.error(f"extraire_followers_spatial ({reseau_nom}): ERR loop ann {i} ('{desc_log}'): {e_loop_ann}")
                logger.error(traceback.format_exc())
                continue 
        
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombres AVANT fusion: {len(number_annotations_list)}")
        for idx, num_ann_log in enumerate(number_annotations_list):
            logger.info(f"  Pre-fusion Num {idx}: '{num_ann_log['text']}' (norm: {num_ann_log['normalized']}), y:{num_ann_log['avg_y']:.0f}, x:{num_ann_log['avg_x']:.0f}")
        
        if len(number_annotations_list) > 1:
            logger.info(f"extraire_followers_spatial ({reseau_nom}): Tentative fusion v2 ({len(number_annotations_list)} nombres)")
            number_annotations_list.sort(key=lambda ann: (ann["avg_y"], ann["avg_x"])) 
            
            merged_numbers_accumulator = []
            processed_indices_merge = set()

            for i in range(len(number_annotations_list)):
                if i in processed_indices_merge:
                    continue

                current_ann = number_annotations_list[i]
                current_group_text_parts = [current_ann["text"]]
                current_group_normalized_parts = [current_ann["normalized"]]
                current_group_min_x = current_ann["min_x"]
                current_group_max_x = current_ann["max_x"]
                current_group_min_y = current_ann["min_y"]
                current_group_max_y = current_ann["max_y"]
                current_group_avg_y = current_ann["avg_y"]
                current_group_indices = {i}
                last_ann_in_current_group = current_ann

                for j in range(i + 1, len(number_annotations_list)):
                    if j in processed_indices_merge:
                        continue
                    
                    next_ann_to_try = number_annotations_list[j]
                    y_diff = abs(last_ann_in_current_group["avg_y"] - next_ann_to_try["avg_y"])
                    x_gap = next_ann_to_try["min_x"] - last_ann_in_current_group["max_x"]
                    
                    if y_diff < 20 and -10 <= x_gap < 35:
                        logger.info(f"    Fusion?: '{''.join(current_group_text_parts)}' + '{next_ann_to_try['text']}' (y_d:{y_diff:.0f}, x_g:{x_gap:.0f})")
                        potential_merged_text = " ".join(current_group_text_parts + [next_ann_to_try["text"]])
                        potential_normalized = normaliser_nombre_followers(potential_merged_text)
                        
                        if potential_normalized and len(potential_normalized) >= len(current_group_normalized_parts[-1]):
                            current_group_text_parts.append(next_ann_to_try["text"])
                            current_group_normalized_parts.append(next_ann_to_try["normalized"])
                            current_group_max_x = max(current_group_max_x, next_ann_to_try["max_x"])
                            current_group_min_y = min(current_group_min_y, next_ann_to_try["min_y"])
                            current_group_max_y = max(current_group_max_y, next_ann_to_try["max_y"])
                            current_group_indices.add(j)
                            last_ann_in_current_group = next_ann_to_try
                            logger.info(f"      -> Fusionné: '{next_ann_to_try['text']}' -> Grp: '{''.join(current_group_text_parts)}' (norm: {potential_normalized})")
                        else:
                            logger.info(f"      -> Fusion REFUSÉE ('{potential_merged_text}' norm: {potential_normalized})")
                            break 
                
                final_merged_text_for_group = " ".join(current_group_text_parts)
                final_normalized_text_for_group = normaliser_nombre_followers(final_merged_text_for_group)

                if final_normalized_text_for_group:
                    merged_numbers_accumulator.append({
                        "text": final_merged_text_for_group,
                        "normalized": final_normalized_text_for_group,
                        "avg_y": current_group_avg_y, 
                        "avg_x": (current_group_min_x + current_group_max_x) / 2,
                        "min_x": current_group_min_x, "max_x": current_group_max_x,
                        "min_y": current_group_min_y, "max_y": current_group_max_y,
                        "annotation": current_ann["annotation"]
                    })
                    processed_indices_merge.update(current_group_indices)
                    logger.info(f"  => Groupe final: '{final_merged_text_for_group}' (norm: {final_normalized_text_for_group}), idx: {current_group_indices}")
                else:
                    merged_numbers_accumulator.append(current_ann)
                    processed_indices_merge.add(i)
                    logger.info(f"  => Original conservé (fusion non norm): '{current_ann['text']}'")
            
            for k_idx, k_ann in enumerate(number_annotations_list):
                if k_idx not in processed_indices_merge:
                    merged_numbers_accumulator.append(k_ann)
                    logger.warning(f"  => Original non fusionné (sécurité): '{k_ann['text']}'")
            
            number_annotations_list = merged_numbers_accumulator
        
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombres APRÈS fusion v2: {len(number_annotations_list)}")
        for idx, num_ann_log in enumerate(number_annotations_list):
            logger.info(f"  Post-fusion Num {idx}: '{num_ann_log['text']}' (norm: {num_ann_log['normalized']}), y:{num_ann_log['avg_y']:.0f}, x:{num_ann_log['avg_x']:.0f}")

        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nb mots-clés: {len(keyword_annotations_list)}, Nb nombres (post-fusion): {len(number_annotations_list)}")

        if not keyword_annotations_list:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucun mot-clé followers.")
            if number_annotations_list:
                number_annotations_list.sort(key=lambda x: int(x.get("normalized", "0").replace(" ","")), reverse=True)
                if number_annotations_list:
                    logger.warning(f"extraire_followers_spatial ({reseau_nom}) (Fallback): Plus grand nombre: {number_annotations_list[0]['normalized']}")
                    return number_annotations_list[0]['normalized']
            return None

        best_candidate_num = None
        min_score = float('inf')
        candidate_details_log = []

        logger.info(f"extraire_followers_spatial ({reseau_nom}): Recherche meilleur candidat.")
        for kw_ann in keyword_annotations_list:
            logger.info(f"  - Pour mot-clé: '{kw_ann['text']}' y:{kw_ann['avg_y']:.0f}, x:{kw_ann['avg_x']:.0f}")
            for num_ann in number_annotations_list:
                y_diff = num_ann['avg_y'] - kw_ann['avg_y'] 
                x_diff = abs(num_ann['avg_x'] - kw_ann['avg_x'])
                
                logger.debug(f"    - Comp avec num: '{num_ann['text']}' (norm: {num_ann['normalized']}) y:{num_ann['avg_y']:.0f}. y_d:{y_diff:.2f}, x_d:{x_diff:.2f}")

                score = float('inf')
                if -20 < y_diff < 100 and x_diff < 200: 
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
                    logger.info(f"      NOUVEAU MEILLEUR: '{num_ann['text']}' (norm: {num_ann['normalized']}), Score: {score:.2f} (kw '{kw_ann['text']}')")

        if candidate_details_log:
            candidate_details_log.sort(key=lambda x: x['score'])
            logger.info(f"extraire_followers_spatial ({reseau_nom}): --- Top 3 candidats ---")
            for cand_log in candidate_details_log[:3]:
                logger.info(f"    Score: {cand_log['score']:.2f}, Num: '{cand_log['norm']}' ('{cand_log['text']}'), Kw: '{cand_log['kw']}'")

        if best_candidate_num:
            logger.info(f"extraire_followers_spatial ({reseau_nom}): MEILLEUR FINAL: {best_candidate_num} score {min_score:.2f}")
            return best_candidate_num
        else:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucun candidat followers trouvé.")
            return None

    except Exception as e_spatial:
        logger.error(f"extraire_followers_spatial ({reseau_nom}): ERREUR MAJEURE: {e_spatial}")
        logger.error(traceback.format_exc())
        return None

def extraire_username(text_annotations, reseau: str, full_text_ocr: str) -> str | None:
    logger.info(f"extraire_username: Début pour réseau: {reseau}")
    if not text_annotations:
        logger.warning("extraire_username: Aucune annotation.")
        return None

    username_found = None
    # full_text_lower = full_text_ocr.lower() # full_text_ocr non utilisé pour l'instant

    known_network_handles = KNOWN_HANDLES.get(reseau, []) 

    # Tentative 1: Chercher un @username proéminent
    for ann in text_annotations[1:]:
        desc = ann.description
        if desc.startswith("@") and len(desc) > 1:
            # Vérifier si ce @handle est connu pour ce réseau
            potential_match = get_close_matches(desc[1:].lower(), [h.lower() for h in known_network_handles], n=1, cutoff=0.85)
            if potential_match:
                username_found = "@" + potential_match[0]
                logger.info(f"extraire_username ({reseau}): Trouvé via @ et KNOWN_HANDLES: {username_found}")
                return corriger_username(username_found, reseau)
            else:
                # Si pas dans known_handles, on le prend quand même s'il est plausible
                if len(desc) > 3 and not any(char in desc for char in " <>[]{}()"): # Éviter les faux positifs
                    username_found = desc
                    logger.info(f"extraire_username ({reseau}): Trouvé via @ (non listé): {username_found}")
                    return corriger_username(username_found, reseau)

    # Tentative 2: Utiliser KNOWN_HANDLES pour rechercher des correspondances partielles dans le texte
    # Utile si le @ est manquant ou mal lu
    for ann in text_annotations[1:]:
        desc_lower = ann.description.lower()
        if len(desc_lower) < 3: continue # Éviter les textes trop courts

        # Chercher une correspondance exacte ou proche d'un handle connu
        potential_matches = get_close_matches(desc_lower, [h.lower() for h in known_network_handles], n=1, cutoff=0.8)
        if potential_matches:
            username_found = potential_matches[0] # Retourne le handle connu, pas le texte OCR brut
            logger.info(f"extraire_username ({reseau}): Trouvé via KNOWN_HANDLES (sans @): {username_found}")
            return corriger_username(username_found, reseau)

    # Logique spécifique si les méthodes génériques échouent
    if reseau == "instagram":
        y_ref = float('inf')
        potential_usernames_above_stats = []
        for ann in text_annotations[1:]:
            if any(kw in ann.description.lower() for kw in ["publications", "followers", "suivi(e)s"]):
                if hasattr(ann.bounding_poly, 'vertices') and ann.bounding_poly.vertices:
                    y_ref = min(y_ref, ann.bounding_poly.vertices[0].y)
        
        if y_ref != float('inf'):
            for ann in text_annotations[1:]:
                ann_text_lower = ann.description.lower()
                if hasattr(ann.bounding_poly, 'vertices') and ann.bounding_poly.vertices and ann.bounding_poly.vertices[3].y < y_ref - 5 and \
                   not any(kw in ann_text_lower for kw in ["publications", "followers", "suivi(e)s", "profil", "modifier", "message", "story", "reels"]) and \
                   len(ann.description) > 2 and not ann.description.isdigit() and "instagram" not in ann_text_lower:
                    potential_usernames_above_stats.append(ann.description)
            
            if potential_usernames_above_stats:
                potential_usernames_above_stats.sort(key=len, reverse=True)
                username_found = potential_usernames_above_stats[0]
                logger.info(f"extraire_username (Instagram fallback): Trouvé au-dessus des stats: {username_found}")
                return corriger_username(username_found, reseau)

    elif reseau == "twitter":
        y_ref_twitter = float('inf')
        potential_usernames_twitter = []
        for ann in text_annotations[1:]:
            if any(kw in ann.description.lower() for kw in ["abonnements", "abonnés"]):
                if hasattr(ann.bounding_poly, 'vertices') and ann.bounding_poly.vertices:
                    y_ref_twitter = min(y_ref_twitter, ann.bounding_poly.vertices[0].y)
        
        if y_ref_twitter != float('inf'):
            for ann in text_annotations[1:]:
                ann_text_lower = ann.description.lower()
                if hasattr(ann.bounding_poly, 'vertices') and ann.bounding_poly.vertices and ann.bounding_poly.vertices[3].y < y_ref_twitter - 5 and \
                   not any(kw in ann_text_lower for kw in ["abonnements", "abonnés", "profil", "éditer", "notifications", "messages"]) and \
                   len(ann.description) > 2 and not ann.description.isdigit() and "twitter" not in ann_text_lower and " x " not in ann_text_lower:
                    potential_usernames_twitter.append(ann.description)
            
            if potential_usernames_twitter:
                potential_usernames_twitter.sort(key=len, reverse=True)
                username_found = potential_usernames_twitter[0]
                logger.info(f"extraire_username (Twitter fallback): Trouvé au-dessus des stats (sans @): {username_found}")
                return corriger_username(username_found, reseau)

    if not username_found:
        logger.warning(f"extraire_username: Nom d_utilisateur non trouvé pour {reseau}.")
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
            logger.info(f"get_text_from_image_vision: Texte extrait. 1er élém: {texts[0].description[:100]}...")
            return texts[0].description, texts
        else:
            logger.warning("get_text_from_image_vision: Aucun texte détecté.")
            return None, None
    except Exception as e:
        logger.error(f"Erreur dans get_text_from_image_vision: {e}")
        logger.error(traceback.format_exc())
        return None, None

def ajouter_donnees_sheet(date_heure: str, operateur: str, reseau_social: str, nom_utilisateur: str, nb_followers: str) -> bool:
    try:
        row = [date_heure, operateur, reseau_social, nom_utilisateur, nb_followers]
        sheet.append_row(row)
        logger.info(f"Données ajoutées à Google Sheets: {row}")
        return True
    except gspread.exceptions.APIError as e_gspread_api:
        logger.error(f"Erreur API Google Sheets lors de l_ajout de données: {e_gspread_api}")
        logger.error(f"Détails de l_erreur API: {e_gspread_api.response.text if e_gspread_api.response else 'Pas de réponse détaillée'}")
        logger.error(traceback.format_exc())
        return False
    except Exception as e:
        logger.error(f"Erreur inconnue lors de l_ajout à Google Sheets: {e}")
        logger.error(traceback.format_exc())
        return False

async def send_telegram_message_to_general_topic(bot: Bot, message_text: str):
    try:
        if not TELEGRAM_GROUP_ID or not TELEGRAM_GENERAL_TOPIC_THREAD_ID:
            logger.error("TELEGRAM_GROUP_ID ou TELEGRAM_GENERAL_TOPIC_THREAD_ID non configuré. Message non envoyé.")
            return
        
        chat_id_to_send = int(TELEGRAM_GROUP_ID)
        thread_id_to_send = int(TELEGRAM_GENERAL_TOPIC_THREAD_ID)
        
        logger.info(f"Envoi du message au sujet General (ChatID: {chat_id_to_send}, ThreadID: {thread_id_to_send}): '{message_text[:100]}...' ")
        await bot.send_message(chat_id=chat_id_to_send, text=message_text, message_thread_id=thread_id_to_send)
        logger.info("Message envoyé avec succès au sujet General.")
    except ValueError as ve:
        logger.error(f"Erreur de valeur pour TELEGRAM_GROUP_ID ou TELEGRAM_GENERAL_TOPIC_THREAD_ID: {ve}. Assurez-vous qu_ils sont des entiers valides.")
    except Exception as e:
        logger.error(f"Erreur lors de l_envoi du message Telegram au sujet General: {e}")
        logger.error(traceback.format_exc())

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo:
        return

    # Vérifier si le message provient d_un sujet "SUIVI..."
    if update.message.is_topic_message and update.message.reply_to_message and \
       update.message.reply_to_message.forum_topic_created and \
       update.message.reply_to_message.forum_topic_created.name.startswith("SUIVI"):
        topic_name = update.message.reply_to_message.forum_topic_created.name
        logger.info(f"Photo reçue dans le sujet: {topic_name}")
    else:
        logger.info("Photo reçue, mais pas dans un sujet pertinent (commençant par SUIVI). Ignorée.")
        return

    user = update.effective_user
    operateur = user.username if user.username else user.first_name
    date_heure = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    file_id = update.message.photo[-1].file_id
    try:
        new_file = await context.bot.get_file(file_id)
        file_path = new_file.file_path
        
        # Télécharger l_image en mémoire
        image_bytes_io = io.BytesIO()
        await new_file.download_to_memory(out=image_bytes_io)
        image_bytes = image_bytes_io.getvalue()
        image_bytes_io.close()
        logger.info(f"Image téléchargée (taille: {len(image_bytes)} bytes).")

        # Traitement OCR
        full_text_ocr, text_annotations = get_text_from_image_vision(image_bytes)

        if not full_text_ocr or not text_annotations:
            msg = f"ÉCHEC OCR pour {operateur} le {date_heure}: Pas de texte détecté."
            logger.warning(msg)
            await send_telegram_message_to_general_topic(context.bot, msg)
            return

        reseau_social = identifier_reseau_social(full_text_ocr)
        logger.info(f"Réseau identifié: {reseau_social}")

        mots_cles_followers = {
            "instagram": ["followers", "abonnés"],
            "twitter": ["abonnés", "followers"],
            "tiktok": ["followers", "abonnés"],
            "facebook": ["amis", "j_aime", "followers"]
        }.get(reseau_social, ["followers", "abonnés"]) # Fallback

        nom_utilisateur = extraire_username(text_annotations, reseau_social, full_text_ocr)
        nb_followers = extraire_followers_spatial(text_annotations, mots_cles_followers, reseau_social)

        logger.info(f"Résultats extraction: User='{nom_utilisateur}', Followers='{nb_followers}'")

        if nom_utilisateur and nb_followers:
            if ajouter_donnees_sheet(date_heure, operateur, reseau_social, nom_utilisateur, nb_followers):
                msg = f"SUCCÈS pour {operateur} ({date_heure}):\nRéseau: {reseau_social}\nCompte: {nom_utilisateur}\nFollowers: {nb_followers}"
                logger.info(msg)
                await send_telegram_message_to_general_topic(context.bot, msg)
            else:
                msg = f"ÉCHEC Enregistrement Google Sheet pour {operateur} ({date_heure}):\nRéseau: {reseau_social}, Compte: {nom_utilisateur}, Followers: {nb_followers}"
                logger.error(msg)
                await send_telegram_message_to_general_topic(context.bot, msg)
        else:
            msg = f"ÉCHEC Extraction pour {operateur} ({date_heure}):\nRéseau: {reseau_social}, Compte: {nom_utilisateur if nom_utilisateur else 'Non trouvé'}, Followers: {nb_followers if nb_followers else 'Non trouvé'}. OCR: {full_text_ocr[:200]}..."
            logger.warning(msg)
            await send_telegram_message_to_general_topic(context.bot, msg)

    except Exception as e:
        error_msg = f"Erreur MAJEURE dans handle_photo pour {operateur} le {date_heure}: {e}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        await send_telegram_message_to_general_topic(context.bot, error_msg)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    # Ici, vous pourriez vouloir informer l_utilisateur ou un administrateur de l_erreur.
    # Par exemple, envoyer un message à un chat spécifique.

def main() -> None:
    if not TOKEN:
        logger.critical("La variable d_environnement TELEGRAM_BOT_TOKEN n_est pas définie. Arrêt du bot.")
        return

    application = Application.builder().token(TOKEN).build()
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_error_handler(error_handler)

    logger.info("Bot démarré en mode polling...")
    application.run_polling()

if __name__ == "__main__":
    main()

