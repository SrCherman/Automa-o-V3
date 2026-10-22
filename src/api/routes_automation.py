"""
routes_automation.py - Endpoints REST para Upload, Controle Multi-Campanha e Limpeza do Banco
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Literal
from src.database.connection import get_db
from src.database.crud import get_or_create_default_campaign, reset_database, get_dashboard_stats, ContactQueryInProgress
from src.ingestion.parser import import_spreadsheet_from_bytes
from src.services.orchestrator import orchestrator
from src.services.export_service import export_contacts_to_excel

router = APIRouter(prefix="/api/automation", tags=["Automação & Dashboard"])

class StartPayload(BaseModel):
    campaign_id: Optional[int] = None
    placa_veiculo: Optional[str] = "IWD0H56"
    valor_veiculo: Optional[str] = "120.000,00"
    etapa_inicial: Literal["AUTO", "MIND7", "ENRIQUECIMENTO", "CREDLINE", "C6"] = "AUTO"

class StopPayload(BaseModel):
    campaign_id: Optional[int] = None

class ResetPayload(BaseModel):
    campaign_id: Optional[int] = None
    confirm: bool = False

@router.get("/state")
def get_state(campaign_id: Optional[int] = None, db: Session = Depends(get_db)):
    state = orchestrator.get_state(campaign_id)
    return {"status": "success", "state": state}

@router.post("/upload")
async def upload_spreadsheet(
    file: UploadFile = File(...),
    campaign_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    if not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="Apenas arquivos .xlsx, .xls ou .csv são permitidos.")

    if not campaign_id:
        c = get_or_create_default_campaign(db)
        campaign_id = c.id

    content = await file.read()
    try:
        res = import_spreadsheet_from_bytes(content, file.filename, db, campaign_id)
        stats = get_dashboard_stats(db, campaign_id)
        return {
            "status": "success",
            "filename": file.filename,
            "campaign_id": campaign_id,
            "resultado": res,
            "stats": stats
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao processar planilha: {exc}")

@router.post("/start")
def start_bot(payload: StartPayload, db: Session = Depends(get_db)):
    camp_id = payload.campaign_id
    if not camp_id:
        c = get_or_create_default_campaign(db)
        camp_id = c.id

    res = orchestrator.start_campaign(
        campaign_id=camp_id,
        placa=payload.placa_veiculo or "IWD0H56",
        valor=payload.valor_veiculo or "120.000,00",
        etapa_inicial=payload.etapa_inicial or "AUTO"
    )
    return res

@router.post("/stop")
def stop_bot(payload: StopPayload = StopPayload()):
    return orchestrator.stop_campaign(campaign_id=payload.campaign_id)

@router.post("/reset-db")
def reset_db_endpoint(payload: ResetPayload, db: Session = Depends(get_db)):
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Confirmação obrigatória. Envie confirm=true para prosseguir.")
    try:
        res = reset_database(db, campaign_id=payload.campaign_id)
    except ContactQueryInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    stats = get_dashboard_stats(db, payload.campaign_id)
    return {**res, "stats": stats}

@router.get("/export")
def export_excel(
    campaign_id: Optional[int] = None,
    status: Optional[str] = "TODOS",
    db: Session = Depends(get_db)
):
    excel_bytes = export_contacts_to_excel(db, campaign_id=campaign_id, status=status)
    filename = f"contatos_qualificados_campanha_{campaign_id or 'todos'}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
