"""
config.py - Configurações Globais do CreditoBot Pro V2
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv()
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
AUDIT_DIR = LOGS_DIR / "auditoria"

STATIC_DIR = BASE_DIR / "src" / "static"
if not STATIC_DIR.exists():
    STATIC_DIR = BASE_DIR / "static"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "database.sqlite"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

# URLS OFICIAIS DOS PORTAIS
MIND7_LOGIN_URL = os.getenv("MIND7_LOGIN_URL", "https://mind-7.org/acesso/")
MIND7_URL = os.getenv("MIND7_URL", "https://mind-7.org/painel/consultas/celular/")
MIND7_CPF_URL = os.getenv("MIND7_CPF_URL", "https://mind-7.org/painel/consultas/cpf/")
CREDLINE_URL = os.getenv("CREDLINE_URL", "https://www.credlineitau.com.br/new-simulator")
C6_URL = "https://c6auto.com.br/originacaolojista/shopkeeper-panel"
C6_USER = os.getenv("C6_USER", "")
C6_PASS = os.getenv("C6_PASS", "")
C6_PLATE = "SEO7H39"
C6_VEHICLE_VALUE = "130.000,00"
C6_OPTIONS = {"entry_value": "", "uf": "PR", "has_cnh": True}

# CREDENCIAIS DE ACESSO AOS PORTAIS
MIND7_USER = os.getenv("MIND7_USER", "")
MIND7_PASS = os.getenv("MIND7_PASS", "")

CREDLINE_USER = os.getenv("CREDLINE_USER", "")
CREDLINE_PASS = os.getenv("CREDLINE_PASS", "")

MIND7_DEBUG_PORT = int(os.getenv("MIND7_DEBUG_PORT", "9223"))
ITAU_DEBUG_PORT = int(os.getenv("ITAU_DEBUG_PORT", "9224"))

HEADLESS_DEFAULT = os.getenv("HEADLESS", "false").lower() in ("true", "1", "yes")
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY", "")

MAX_WORKERS = int(os.getenv("MAX_WORKERS", "2"))
