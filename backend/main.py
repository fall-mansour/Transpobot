"""
TranspoBot - Backend FastAPI
Gestion de Transport Urbain avec IA
ESP/UCAD - Licence 3 GLSi
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
import mysql.connector
import os
import json
import re
from openai import OpenAI
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TranspoBot API",
    description="API de gestion de transport urbain avec assistant IA",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# CONFIGURATION
# ============================================================

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "transpobot"),
    "charset": "utf8mb4",
    "autocommit": True
}

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ============================================================
# DATABASE
# ============================================================

def get_db():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as e:
        logger.error(f"DB connection error: {e}")
        raise HTTPException(status_code=503, detail=f"Connexion DB impossible: {str(e)}")

def execute_query(sql: str, params=None):
    conn = get_db()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params or ())
        results = cursor.fetchall()
        return results
    finally:
        cursor.close()
        conn.close()

# ============================================================
# SCHÉMA DB POUR LLM  ← MIS À JOUR
# ============================================================

DB_SCHEMA = """
BASE DE DONNÉES: transpobot (MySQL)

TABLE vehicules:
  - id INT (PK)
  - immatriculation VARCHAR(20) - ex: 'DK-1234-AB'
  - type ENUM('bus','minibus','taxi')
  - capacite INT - nombre de passagers max
  - statut ENUM('actif','maintenance','hors_service')
  - kilometrage INT
  - date_acquisition DATE
  - created_at TIMESTAMP

TABLE chauffeurs:
  - id INT (PK)
  - nom VARCHAR(100)
  - prenom VARCHAR(100)
  - telephone VARCHAR(20)
  - numero_permis VARCHAR(30)
  - categorie_permis VARCHAR(5)
  - disponibilite BOOLEAN (1=disponible, 0=indisponible)
  - vehicule_id INT (FK -> vehicules.id)
  - date_embauche DATE
  - created_at TIMESTAMP

TABLE lignes:
  - id INT (PK)
  - code VARCHAR(10) - ex: 'L1', 'L2'
  - nom VARCHAR(100)
  - origine VARCHAR(100)
  - destination VARCHAR(100)
  - distance_km DECIMAL(6,2)
  - duree_minutes INT

TABLE tarifs:
  - id INT (PK)
  - ligne_id INT (FK -> lignes.id)
  - type_client ENUM('normal','etudiant','senior')
  - prix DECIMAL(10,2) - en FCFA

TABLE trajets:
  - id INT (PK)
  - ligne_id INT (FK -> lignes.id)
  - chauffeur_id INT (FK -> chauffeurs.id)
  - vehicule_id INT (FK -> vehicules.id)
  - date_heure_depart DATETIME
  - date_heure_arrivee DATETIME
  - statut ENUM('planifie','en_cours','termine','annule')
  - nb_passagers INT
  - recette DECIMAL(10,2) - en FCFA
  - created_at TIMESTAMP

TABLE incidents:
  - id INT (PK)
  - trajet_id INT (FK -> trajets.id)
  - type ENUM('panne','accident','retard','autre')
  - description TEXT
  - gravite ENUM('faible','moyen','grave')
  - date_incident DATETIME
  - resolu BOOLEAN (0=non résolu, 1=résolu)
  - created_at TIMESTAMP
"""

SYSTEM_PROMPT = """Tu es TranspoBot, un assistant IA expert en analyse de données de transport urbain pour la société TranspoSN à Dakar, Sénégal.

Tu as accès à une base de données MySQL avec le schéma suivant:
""" + DB_SCHEMA + """

RÈGLES STRICTES:
1. Tu génères UNIQUEMENT des requêtes SELECT (jamais INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE)
2. Si la question est hors sujet ou nécessite une modification, réponds poliment sans SQL
3. Toujours répondre en français
4. Formater les montants en FCFA
5. Les dates sont en fuseau horaire de Dakar (Africa/Dakar)

FORMAT DE RÉPONSE:
- Si tu génères du SQL, réponds UNIQUEMENT avec ce format JSON exact:
{"type": "sql", "query": "SELECT ...", "explanation": "Explication courte"}
- Si tu réponds sans SQL (hors sujet, question générale):
{"type": "text", "response": "Ta réponse ici"}

EXEMPLES:
- "Combien de trajets cette semaine?" -> {"type":"sql","query":"SELECT COUNT(*) as total FROM trajets WHERE date_heure_depart >= DATE_SUB(NOW(), INTERVAL 7 DAY) AND statut='termine'","explanation":"Nombre de trajets terminés sur les 7 derniers jours"}
- "Quel chauffeur a le plus d'incidents ce mois?" -> {"type":"sql","query":"SELECT c.nom, c.prenom, COUNT(i.id) as nb_incidents FROM incidents i JOIN trajets t ON i.trajet_id=t.id JOIN chauffeurs c ON t.chauffeur_id=c.id WHERE MONTH(i.date_incident)=MONTH(NOW()) GROUP BY c.id ORDER BY nb_incidents DESC LIMIT 1","explanation":"Chauffeur avec le plus d'incidents ce mois"}
"""

# ============================================================
# MODÈLES PYDANTIC
# ============================================================

class ChatMessage(BaseModel):
    message: str
    history: Optional[List[dict]] = []

class ChatResponse(BaseModel):
    response: str
    sql_query: Optional[str] = None
    results: Optional[List[dict]] = None
    columns: Optional[List[str]] = None
    explanation: Optional[str] = None

# ============================================================
# ROUTES API - DONNÉES
# ============================================================

@app.get("/api/dashboard/kpis")
def get_kpis():
    """Indicateurs clés du tableau de bord"""
    stats = {}
    
    # Total véhicules par statut
    veh = execute_query("SELECT statut, COUNT(*) as count FROM vehicules GROUP BY statut")
    stats["vehicules"] = {v["statut"]: v["count"] for v in veh}
    stats["vehicules"]["total"] = sum(v["count"] for v in veh)
    
    # ✅ CORRIGÉ: statut → disponibilite (BOOLEAN)
    chauf = execute_query("SELECT COUNT(*) as total, SUM(disponibilite=1) as actifs FROM chauffeurs")[0]
    stats["chauffeurs"] = {"total": chauf["total"], "actifs": int(chauf["actifs"] or 0)}
    
    # Trajets aujourd'hui
    today_trajets = execute_query("""
        SELECT 
            COUNT(*) as total,
            SUM(statut='termine') as termines,
            SUM(statut='en_cours') as en_cours,
            SUM(statut='planifie') as planifies,
            SUM(statut='annule') as annules,
            COALESCE(SUM(recette),0) as recette_totale
        FROM trajets WHERE DATE(date_heure_depart) = CURDATE()
    """)[0]
    stats["trajets_aujourd_hui"] = {k: int(v or 0) for k, v in today_trajets.items()}
    
    # Trajets cette semaine
    week_trajets = execute_query("""
        SELECT COUNT(*) as total, COALESCE(SUM(recette),0) as recette
        FROM trajets WHERE date_heure_depart >= DATE_SUB(NOW(), INTERVAL 7 DAY) AND statut='termine'
    """)[0]
    stats["trajets_semaine"] = {"total": int(week_trajets["total"]), "recette": float(week_trajets["recette"])}
    
    # ✅ CORRIGÉ: statut != 'resolu' → resolu = 0 (BOOLEAN)
    incidents = execute_query("SELECT COUNT(*) as total FROM incidents WHERE resolu = 0")[0]
    stats["incidents_ouverts"] = int(incidents["total"])
    
    return {"status": "ok", "data": stats}

@app.get("/api/vehicules")
def get_vehicules(statut: Optional[str] = None):
    sql = "SELECT * FROM vehicules"
    if statut:
        sql += f" WHERE statut = '{statut}'"
    sql += " ORDER BY created_at DESC"
    return {"data": execute_query(sql)}

@app.get("/api/chauffeurs")
def get_chauffeurs():
    sql = """
        SELECT c.*, 
            COUNT(t.id) as total_trajets,
            COALESCE(SUM(CASE WHEN t.statut='termine' THEN 1 ELSE 0 END),0) as trajets_termines
        FROM chauffeurs c
        LEFT JOIN trajets t ON c.id = t.chauffeur_id
        GROUP BY c.id ORDER BY c.nom
    """
    return {"data": execute_query(sql)}

@app.get("/api/trajets")
def get_trajets(limit: int = 20):
    # ✅ CORRIGÉ: suppression de v.marque et v.modele (n'existent plus)
    sql = f"""
        SELECT t.*, 
            l.code as ligne_code, l.nom as ligne_nom,
            v.immatriculation, v.type as vehicule_type,
            c.nom as chauffeur_nom, c.prenom as chauffeur_prenom
        FROM trajets t
        JOIN lignes l ON t.ligne_id = l.id
        JOIN vehicules v ON t.vehicule_id = v.id
        JOIN chauffeurs c ON t.chauffeur_id = c.id
        ORDER BY t.date_heure_depart DESC LIMIT {limit}
    """
    return {"data": execute_query(sql)}

@app.get("/api/incidents")
def get_incidents(limit: int = 20):
    # ✅ CORRIGÉ: suppression de i.statut (remplacé par i.resolu BOOLEAN)
    sql = f"""
        SELECT i.*,
            c.nom as chauffeur_nom, c.prenom as chauffeur_prenom,
            l.code as ligne_code
        FROM incidents i
        JOIN trajets t ON i.trajet_id = t.id
        JOIN chauffeurs c ON t.chauffeur_id = c.id
        JOIN lignes l ON t.ligne_id = l.id
        ORDER BY i.date_incident DESC LIMIT {limit}
    """
    return {"data": execute_query(sql)}

@app.get("/api/lignes")
def get_lignes():
    sql = """
        SELECT l.*, 
            COUNT(t.id) as total_trajets,
            AVG(t.nb_passagers) as moy_passagers
        FROM lignes l
        LEFT JOIN trajets t ON l.id = t.ligne_id AND t.statut='termine'
        GROUP BY l.id ORDER BY l.code
    """
    return {"data": execute_query(sql)}

# ============================================================
# ROUTE CHATBOT
# ============================================================

@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatMessage):
    user_message = payload.message.strip()
    
    if not user_message:
        raise HTTPException(status_code=400, detail="Message vide")
    
    # Mode fallback si pas d'API key
    if not client:
        return await _fallback_chat(user_message)
    
    try:
        # Construction de l'historique
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for h in (payload.history or [])[-6:]:  # 6 derniers échanges
            messages.append(h)
        messages.append({"role": "user", "content": user_message})
        
        # Appel LLM
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.1,
            max_tokens=500
        )
        
        raw = completion.choices[0].message.content.strip()
        logger.info(f"LLM raw response: {raw}")
        
        # Nettoyage JSON
        clean = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(clean)
        
        if parsed.get("type") == "sql":
            sql = parsed["query"]
            explanation = parsed.get("explanation", "")
            
            # SÉCURITÉ: vérifier SELECT only
            if not _is_safe_query(sql):
                return ChatResponse(
                    response="⛔ Désolé, je ne peux exécuter que des requêtes SELECT pour la sécurité des données.",
                    sql_query=sql
                )
            
            # Exécution
            results = execute_query(sql)
            columns = list(results[0].keys()) if results else []
            
            # Formatage réponse naturelle
            natural = _format_natural_response(user_message, results, explanation)
            
            return ChatResponse(
                response=natural,
                sql_query=sql,
                results=[{k: str(v) if isinstance(v, (datetime,)) else v for k, v in row.items()} for row in results],
                columns=columns,
                explanation=explanation
            )
        else:
            return ChatResponse(response=parsed.get("response", raw))
    
    except json.JSONDecodeError:
        # Le LLM a répondu du texte normal
        return ChatResponse(response=raw)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _fallback_chat(message: str) -> ChatResponse:
    """Réponses de démo sans LLM"""
    msg_lower = message.lower()
    
    if any(w in msg_lower for w in ["trajet", "voyage", "semaine"]):
        results = execute_query("SELECT COUNT(*) as total_trajets, COALESCE(SUM(recette),0) as recette_totale FROM trajets WHERE date_heure_depart >= DATE_SUB(NOW(), INTERVAL 7 DAY) AND statut='termine'")
        r = results[0] if results else {}
        return ChatResponse(
            response=f"📊 Cette semaine: **{r.get('total_trajets', 0)} trajets terminés** pour une recette de **{float(r.get('recette_totale',0)):,.0f} FCFA**",
            sql_query="SELECT COUNT(*) as total_trajets, SUM(recette) as recette_totale FROM trajets WHERE date_heure_depart >= DATE_SUB(NOW(), INTERVAL 7 DAY) AND statut='termine'",
            results=[{k: str(v) for k,v in r.items()}],
            columns=list(r.keys())
        )
    elif any(w in msg_lower for w in ["incident", "accident"]):
        results = execute_query("SELECT c.nom, c.prenom, COUNT(i.id) as nb_incidents FROM incidents i JOIN trajets t ON i.trajet_id=t.id JOIN chauffeurs c ON t.chauffeur_id=c.id GROUP BY c.id ORDER BY nb_incidents DESC LIMIT 5")
        return ChatResponse(
            response=f"⚠️ Chauffeur avec le plus d'incidents: **{results[0]['prenom']} {results[0]['nom']}** ({results[0]['nb_incidents']} incidents)" if results else "Aucun incident trouvé.",
            sql_query="SELECT c.nom, c.prenom, COUNT(i.id) as nb FROM incidents i JOIN trajets t ON i.trajet_id=t.id JOIN chauffeurs c ON t.chauffeur_id=c.id GROUP BY c.id ORDER BY nb DESC LIMIT 5",
            results=[{k: str(v) for k,v in r.items()} for r in results[:5]],
            columns=["nom","prenom","nb_incidents"]
        )
    elif any(w in msg_lower for w in ["vehicule", "véhicule", "maintenance"]):
        # ✅ CORRIGÉ: suppression de marque et modele, ajout de type
        results = execute_query("SELECT immatriculation, type, statut, kilometrage FROM vehicules WHERE statut != 'actif'")
        return ChatResponse(
            response=f"🚌 **{len(results)} véhicule(s)** nécessitent attention (maintenance/hors service).",
            results=[{k: str(v) for k,v in r.items()} for r in results],
            columns=["immatriculation","type","statut","kilometrage"]
        )
    else:
        return ChatResponse(
            response="👋 Bonjour! Je suis **TranspoBot**, votre assistant IA de transport. Configurez une clé API OpenAI pour des réponses intelligentes. En mode démo, essayez: *'Combien de trajets cette semaine?'*, *'Quels incidents ce mois?'*, *'Véhicules en maintenance?'*"
        )


def _is_safe_query(sql: str) -> bool:
    """Vérification de sécurité: SELECT uniquement"""
    sql_clean = sql.strip().upper()
    forbidden = ['INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'TRUNCATE', 'EXEC', 'EXECUTE']
    if not sql_clean.startswith('SELECT'):
        return False
    for keyword in forbidden:
        if re.search(r'\b' + keyword + r'\b', sql_clean):
            return False
    return True


def _format_natural_response(question: str, results: list, explanation: str) -> str:
    """Formate une réponse naturelle en français"""
    if not results:
        return "🔍 Aucun résultat trouvé pour cette requête."
    
    row = results[0]
    q_lower = question.lower()
    
    # Cas count
    for k, v in row.items():
        if 'count' in k.lower() or 'total' in k.lower() or 'nb' in k.lower():
            label = explanation or "résultats"
            return f"📊 **{v}** {label}"
    
    # Cas montant/recette
    if any('recette' in k or 'montant' in k or 'prix' in k for k in row.keys()):
        parts = []
        for k, v in row.items():
            if isinstance(v, (int, float)):
                parts.append(f"**{float(v):,.0f} FCFA**" if 'recette' in k or 'prix' in k else f"**{v}**")
            else:
                parts.append(f"**{v}**")
        return f"💰 {' — '.join(parts)}"
    
    # Réponse générale
    if len(results) == 1:
        vals = " | ".join(f"**{v}**" for v in row.values() if v is not None)
        return f"✅ {vals}"
    else:
        return f"✅ **{len(results)} résultat(s)** trouvés. Voir le tableau ci-dessous."


@app.get("/health")
def health():
    return {"status": "ok", "service": "TranspoBot API", "version": "1.0.0"}

# Servir le frontend
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")