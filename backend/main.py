"""
SmartTech Central - Backend Final
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
import logging
from openai import OpenAI
from datetime import datetime

# ============================================================
# 1. CONFIGURATION & LOGGING
# ============================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="SmartTech Central API",
    description="API de gestion de transport urbain avec assistant IA",
    version="1.0.0"
)

# Configuration du CORS pour le Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration Database (Aiven / Render)
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 17219)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME", "defaultdb"),
    "ssl_disabled": False,  # Requis pour Aiven
    "charset": "utf8mb4",
    "autocommit": True
}

# Initialisation OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ============================================================
# 2. FONCTIONS DE BASE DE DONNÉES
# ============================================================

def get_db():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as e:
        logger.error(f"❌ Erreur Connexion DB: {e}")
        raise HTTPException(status_code=503, detail="Base de données inaccessible")

def execute_query(sql: str, params=None):
    conn = get_db()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params or ())
        results = cursor.fetchall()
        return results
    except Exception as e:
        logger.error(f"⚠️ Erreur SQL: {e}")
        return []
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'conn' in locals(): conn.close()

# ============================================================
# 3. ROUTES NAVIGATION (SERVIR L'HTML)
# ============================================================

@app.get("/")
async def read_index():
    paths = ["index.html", "frontend/index.html"]
    for path in paths:
        if os.path.exists(path):
            return FileResponse(path)
    raise HTTPException(status_code=404, detail="index.html non trouvé")

# ============================================================
# 4. ROUTES API - DONNÉES & DASHBOARD
# ============================================================

@app.get("/api/dashboard/kpis")
def get_kpis():
    try:
        # Véhicules
        veh = execute_query("SELECT statut, COUNT(*) as count FROM vehicules GROUP BY statut")
        v_stats = {v["statut"]: v["count"] for v in veh}
        
        # Chauffeurs
        ch_res = execute_query("SELECT COUNT(*) as total, SUM(disponibilite=1) as actifs FROM chauffeurs")
        chauf = ch_res[0] if ch_res else {"total": 0, "actifs": 0}
        
        # Trajets aujourd'hui
        tr_res = execute_query("""
            SELECT COUNT(*) as total, SUM(statut='termine') as termines, 
            COALESCE(SUM(recette),0) as recette_totale
            FROM trajets WHERE DATE(date_heure_depart) = CURDATE()
        """)
        traj = tr_res[0] if tr_res else {"total": 0, "termines": 0, "recette_totale": 0}

        # Incidents
        inc_res = execute_query("SELECT COUNT(*) as total FROM incidents WHERE resolu = 0")
        inc = inc_res[0] if inc_res else {"total": 0}

        return {
            "status": "ok",
            "data": {
                "vehicules": {**v_stats, "total": sum(v_stats.values()) if v_stats else 0},
                "chauffeurs": {"total": chauf["total"] or 0, "actifs": int(chauf["actifs"] or 0)},
                "trajets_aujourd_hui": {k: int(v or 0) for k, v in traj.items()},
                "incidents_ouverts": int(inc["total"] or 0)
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/lignes")
def get_lignes():
    sql = """
        SELECT l.*, COUNT(t.id) as total_trajets, AVG(t.nb_passagers) as moy_passagers
        FROM lignes l LEFT JOIN trajets t ON l.id = t.ligne_id 
        GROUP BY l.id ORDER BY l.code
    """
    return {"data": execute_query(sql)}

@app.get("/api/vehicules")
def get_vehicules():
    return {"data": execute_query("SELECT * FROM vehicules ORDER BY created_at DESC")}

@app.get("/api/chauffeurs")
def get_chauffeurs():
    return {"data": execute_query("SELECT * FROM chauffeurs ORDER BY nom ASC")}

@app.get("/api/trajets")
def get_trajets(limit: int = 30):
    sql = """
        SELECT t.*, l.code as ligne_code, v.immatriculation, 
        c.nom as chauffeur_nom, c.prenom as chauffeur_prenom
        FROM trajets t
        LEFT JOIN lignes l ON t.ligne_id = l.id
        LEFT JOIN vehicules v ON t.vehicule_id = v.id
        LEFT JOIN chauffeurs c ON t.chauffeur_id = c.id
        ORDER BY t.date_heure_depart DESC LIMIT %s
    """
    return {"data": execute_query(sql, (limit,))}

@app.get("/api/incidents")
def get_incidents(limit: int = 30):
    return {"data": execute_query("SELECT * FROM incidents ORDER BY date_incident DESC LIMIT %s", (limit,))}

# ============================================================
# 5. LOGIQUE DU CHATBOT (APPEL RÉEL OPENAI)
# ============================================================

class ChatMessage(BaseModel):
    message: str
    history: Optional[List[dict]] = []

@app.post("/api/chat")
async def chat(payload: ChatMessage):
    if not client:
        logger.error("❌ OpenAI Client non configuré (Clé manquante)")
        return {"response": "Assistant en mode démo. Vérifiez votre clé API sur Render."}
    
    try:
        # Contexte système
        messages = [{
            "role": "system", 
            "content": "Tu es l'assistant intelligent de SmartTech Central. Tu aides à gérer le transport urbain. Réponds de manière utile et polie."
        }]
        
        # Historique
        if payload.history:
            messages.extend(payload.history[-5:])
            
        # Message utilisateur
        messages.append({"role": "user", "content": payload.message})

        # Appel API
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.7
        )

        return {"response": response.choices[0].message.content}

    except Exception as e:
        logger.error(f"❌ Erreur OpenAI: {e}")
        if "insufficient_quota" in str(e):
            return {"response": "Erreur : Quota OpenAI épuisé. Veuillez vérifier vos crédits."}
        return {"response": "Désolé, je ne peux pas répondre pour le moment."}

# ============================================================
# 6. LANCEMENT & STATIQUES
# ============================================================

# Dossier static (CSS, JS)
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
elif os.path.exists("frontend/static"):
    app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

@app.get("/health")
def health():
    return {"status": "ok", "db": "connected", "time": datetime.now()}
