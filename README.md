# 🚌 TranspoBot — Gestion de Transport Urbain avec IA

**ESP/UCAD — Licence 3 GLSi | Cours : Intégration de l'IA dans les SI**

![Version](https://img.shields.io/badge/version-1.0.0-blue)
![Python](https://img.shields.io/badge/python-3.11-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)
![MySQL](https://img.shields.io/badge/MySQL-8.0-orange)

---

## 📋 Description

TranspoBot est une application web de gestion de transport urbain intégrant un assistant conversationnel basé sur GPT-4o-mini. Les gestionnaires peuvent interroger les données en langage naturel via un chatbot IA (Text-to-SQL).

## 🏗️ Architecture

```
transpobot/
├── backend/
│   ├── main.py           # API FastAPI + logique LLM
│   ├── requirements.txt  # Dépendances Python
│   └── .env.example      # Variables d'environnement
├── frontend/
│   └── index.html        # SPA (Dashboard + Chat)
├── sql/
│   └── schema.sql        # Schéma + données de test
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## ⚡ Installation rapide (Docker)

```bash
# 1. Cloner le projet
git clone https://github.com/votre-repo/transpobot.git
cd transpobot

# 2. Configurer les variables
cp backend/.env.example backend/.env
# Éditer .env et ajouter votre OPENAI_API_KEY

# 3. Lancer avec Docker Compose
OPENAI_API_KEY=sk-... docker-compose up -d

# 4. Accéder à l'application
open http://localhost:8000
```

## 🛠️ Installation manuelle

### Prérequis
- Python 3.11+
- MySQL 8.x
- Compte OpenAI (optionnel, mode démo disponible)

### 1. Base de données

```sql
mysql -u root -p < sql/schema.sql
```

### 2. Backend

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# Éditer .env avec vos paramètres

uvicorn main:app --reload --port 8000
```

### 3. Accès

Ouvrez http://localhost:8000 dans votre navigateur.

## 🤖 Fonctionnement du Chatbot IA

```
Utilisateur → Question en français
     ↓
GPT-4o-mini → Génère requête SQL
     ↓
MySQL → Exécute SELECT seulement
     ↓
Réponse naturelle + tableau de résultats
```

### Exemples de dialogues

| Question | SQL généré |
|----------|-----------|
| "Combien de trajets cette semaine ?" | `SELECT COUNT(*) FROM trajets WHERE date_heure_depart >= DATE_SUB(NOW(), INTERVAL 7 DAY) AND statut='termine'` |
| "Quel chauffeur a le plus d'incidents ?" | `SELECT c.nom, c.prenom, COUNT(i.id) as nb FROM incidents i JOIN trajets... GROUP BY c.id ORDER BY nb DESC LIMIT 1` |

## 🔒 Sécurité

- Seules les requêtes **SELECT** sont autorisées
- Validation regex avant exécution
- Mots-clés dangereux bloqués : INSERT, UPDATE, DELETE, DROP, ALTER...

## 📦 Déploiement sur Railway

```bash
# Installer Railway CLI
npm install -g @railway/cli

# Déployer
railway login
railway init
railway add --database mysql
railway up

# Variables d'environnement
railway variables set OPENAI_API_KEY=sk-...
```

## 🎯 Fonctionnalités

- ✅ Dashboard avec KPIs temps réel
- ✅ Gestion flotte véhicules
- ✅ Gestion chauffeurs
- ✅ Suivi trajets
- ✅ Gestion incidents
- ✅ Chatbot IA Text-to-SQL (GPT-4o-mini)
- ✅ Mode démo sans API key
- ✅ Sécurité SELECT-only
- ✅ Interface responsive

## 👥 Auteurs

- **Idriss** — Développeur fullstack
- ESP/UCAD — Licence 3 GLSi 2025-2026

## 📧 Contact enseignant

Pr. Ahmath Bamba MBACKE — ahmathbamba.mbacke@esp.sn
