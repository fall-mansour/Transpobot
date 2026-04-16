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

# ============================================================
# CONFIGURATION & INITIALISATION
# ============================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TranspoBot API",
    description="API de gestion de transport urbain avec assistant IA",
    version="1.0.0"
)

# Configuration du CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration Database (Ajout du support SSL pour Aiven)
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 17219)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME", "defaultdb"),
    "ssl_disabled": False, # Indispensable pour Aiven
    "charset": "utf8mb4",
    "autocommit": True
}

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ============================================================
# DATABASE UTILS
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
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql, params or ())
        results = cursor.fetchall()
        return results
    finally:
        cursor.close()
        conn.close()

# ============================================================
# ROUTES NAVIGATION (FRONTEND)
# ============================================================

@app.get("/")
async def read_index():
    # On cherche l'index à la racine d'abord, sinon dans un dossier frontend
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    elif os.path.exists("frontend/index.html"):
        return FileResponse("frontend/index.html")
    else:
        raise HTTPException(status_code=404, detail="index.html non trouvé")

# ============================================================
# ROUTES API (KPIs, Véhicules, etc.)
# ============================================================

@app.get("/api/dashboard/kpis")
def get_kpis():
    stats = {}
    veh = execute_query("SELECT statut, COUNT(*) as count FROM vehicules GROUP BY statut")
    stats["vehicules"] = {v["statut"]: v["count"] for v in veh}
    stats["vehicules"]["total"] = sum(v["count"] for v in veh)
    
    chauf = execute_query("SELECT COUNT(*) as total, SUM(disponibilite=1) as actifs FROM chauffeurs")[0]
    stats["chauffeurs"] = {"total": chauf["total"], "actifs": int(chauf["actifs"] or 0)}
    
    today_trajets = execute_query("""
        SELECT COUNT(*) as total, SUM(statut='termine') as termines, 
        COALESCE(SUM(recette),0) as recette_totale
        FROM trajets WHERE DATE(date_heure_depart) = CURDATE()
    """)[0]
    stats["trajets_aujourd_hui"] = {k: int(v or 0) for k, v in today_trajets.items()}
    
    incidents = execute_query("SELECT COUNT(*) as total FROM incidents WHERE resolu = 0")[0]
    stats["incidents_ouverts"] = int(incidents["total"])
    return {"status": "ok", "data": stats}

@app.get("/api/vehicules")
def get_vehicules():
    return {"data": execute_query("SELECT * FROM vehicules ORDER BY created_at DESC")}

# --- (Ajoute ici tes autres routes comme /api/trajets si besoin) ---

# ============================================================
# CHATBOT LOGIC
# ============================================================

class ChatMessage(BaseModel):
    message: str
    history: Optional[List[dict]] = []

class ChatResponse(BaseModel):
    response: str
    sql_query: Optional[str] = None
    results: Optional[List[dict]] = None
    explanation: Optional[str] = None

@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatMessage):
    # Logique simplifiée pour l'exemple
    if not client:
        return ChatResponse(response="Assistant en mode démo (Clé OpenAI manquante).")
    
    # ... (Garde ta logique OpenAI ici) ...
    return ChatResponse(response="Message reçu")

# ============================================================
# LANCEMENT
# ============================================================

@app.get("/health")
def health():
    return {"status": "ok"}

# Montage des fichiers statiques (CSS, JS, Images)
# On vérifie si le dossier existe pour éviter un crash au démarrage
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
elif os.path.exists("frontend/static"):
    app.mount("/static", StaticFiles(directory="frontend/static"), name="static")