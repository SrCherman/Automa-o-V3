"""
routes_campaigns.py - Endpoints REST para Gestão de Campanhas (Isolamento de Fluxos)
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from src.database.connection import get_db
from src.database.crud import list_campaigns, create_campaign, delete_campaign, get_or_create_default_campaign, ContactQueryInProgress

router = APIRouter(prefix="/api/campaigns", tags=["Campanhas"])

class CampaignCreate(BaseModel):
    name: str
    description: Optional[str] = ""

@router.get("/")
def get_campaigns(db: Session = Depends(get_db)):
    get_or_create_default_campaign(db)
    campaigns = list_campaigns(db)
    return {"status": "success", "campaigns": campaigns}

@router.post("/")
def add_campaign(payload: CampaignCreate, db: Session = Depends(get_db)):
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="Nome da campanha é obrigatório.")
    campaign = create_campaign(db, payload.name.strip(), payload.description.strip())
    return {"status": "success", "campaign": campaign}

@router.delete("/{campaign_id}")
def remove_campaign(campaign_id: int, db: Session = Depends(get_db)):
    try:
        ok = delete_campaign(db, campaign_id)
    except ContactQueryInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Campanha não encontrada.")
    return {"status": "success", "message": "Campanha removida com sucesso."}
