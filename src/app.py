"""
app.py - Ponto de Entrada Principal da Aplicação CreditoBot Pro V2
"""

import sys
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import asyncio
from contextlib import asynccontextmanager
from src.config import STATIC_DIR
from src.database.connection import init_db
from src.api.routes_campaigns import router as campaigns_router
from src.api.routes_contacts import router as contacts_router
from src.api.routes_automation import router as automation_router
from src.api.websockets import router as ws_router
from src.services.orchestrator import ws_manager

from src.utils.logger import setup_logging

# Logging Organizado
setup_logging()

# Inicializar Banco de Dados SQLite (WAL Mode) + WebSocket Event Loop
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        ws_manager.set_loop(asyncio.get_event_loop())
    except RuntimeError:
        pass
    yield

app = FastAPI(
    title="CreditoBot Pro V2 - Plataforma de Qualificação de Crédito",
    description="Sistema comercial de automação de consultas de crédito com Banco de Dados e Automação Headless.",
    version="2.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Incluir Rotas API
app.include_router(campaigns_router)
app.include_router(contacts_router)
app.include_router(automation_router)
app.include_router(ws_router)

# Arquivos Estáticos e SPA
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
def read_root():
    return FileResponse(STATIC_DIR / "index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.app:app", host="0.0.0.0", port=8000, reload=True)
