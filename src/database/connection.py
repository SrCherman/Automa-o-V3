"""
connection.py - Gerenciamento de Conexão SQLite com WAL Mode para Alta Performance
"""

import sqlite3
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
from src.config import DATABASE_URL, DB_PATH

Base = declarative_base()

# Configuração do Engine SQLAlchemy com SQLite
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    echo=False
)

# Habilitar WAL mode e Foreign Keys no SQLite para suportar leituras/escritas concorrentes ultrarrápidas
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA busy_timeout=10000;")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Inicializa as tabelas do banco de dados se não existirem."""
    from src.database import models  # noqa
    Base.metadata.create_all(bind=engine)
    # create_all não acrescenta colunas às bases SQLite já existentes.
    existing = {c["name"] for c in inspect(engine).get_columns("contacts")}
    additions = {
        "birth_date": "VARCHAR(10)",
        "enrichment_status": "VARCHAR(30) DEFAULT 'PENDENTE'",
        "enrichment_attempts": "INTEGER DEFAULT 0",
        "enrichment_observation": "TEXT",
        "enriched_at": "DATETIME",
        "mind7_attempts": "INTEGER DEFAULT 0",
        "c6_status": "VARCHAR(40) DEFAULT 'PENDENTE'",
        "c6_financed_value": "VARCHAR(50)",
        "c6_observation": "TEXT",
        "c6_attempts": "INTEGER DEFAULT 0",
        "c6_processed_at": "DATETIME",
    }
    missing = {name: ddl for name, ddl in additions.items() if name not in existing}
    if missing and engine.url.get_backend_name() == "sqlite":
        database = engine.url.database
        if database and database != ":memory:":
            from pathlib import Path
            backup = Path(database).with_suffix(".before_c6.sqlite")
            if not backup.exists():
                with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
                    source.backup(target)
    with engine.begin() as conn:
        for name, ddl in missing.items():
            conn.execute(text(f'ALTER TABLE contacts ADD COLUMN "{name}" {ddl}'))
        if "c6_status" in missing:
            conn.execute(text("UPDATE contacts SET c6_status = 'AGUARDANDO CPF' WHERE cpf IS NULL OR cpf = ''"))
            conn.execute(text("UPDATE contacts SET c6_status = 'AGUARDANDO DADOS', c6_observation = 'Data de nascimento pendente; executar enriquecimento por CPF' WHERE cpf IS NOT NULL AND cpf != '' AND birth_date IS NULL"))
