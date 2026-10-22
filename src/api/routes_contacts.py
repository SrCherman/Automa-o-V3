"""
routes_contacts.py - Endpoints REST para CRUD, Edição, Exclusão e Transferência de Leads
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, field_validator
from typing import Optional, List, Dict, Any, Literal
from src.automation.mind7_scraper import normalizar_nascimento
from src.database.connection import get_db
from src.database.crud import (
    list_contacts, get_contact_by_id, update_contact, delete_contact,
    bulk_delete_contacts, delete_all_contacts, transfer_contacts_to_campaign,
    bulk_update_contacts_status, get_dashboard_stats, ContactQueryInProgress
)

router = APIRouter(prefix="/api/contacts", tags=["Contatos"])

class BulkStatusPayload(BaseModel):
    contact_ids: List[int]
    credline_status: str

class ContactUpdate(BaseModel):
    phone: Optional[str] = None
    whatsapp_name: Optional[str] = None
    contact_name: Optional[str] = None
    clean_name: Optional[str] = None
    cpf: Optional[str] = None
    mind7_name: Optional[str] = None
    cep: Optional[str] = None
    house_number: Optional[str] = None
    birth_date: Optional[str] = None
    retry_c6: bool = False
    enrichment_status: Optional[Literal["PENDENTE", "CONCLUIDO", "INCOMPLETO", "SEM DADOS", "ERRO"]] = None
    credline_status: Optional[str] = None
    financed_value: Optional[str] = None
    observation: Optional[str] = None

    @field_validator("birth_date")
    @classmethod
    def valid_birth_date(cls, value):
        if not value:
            return None
        normalized = normalizar_nascimento(value)
        if not normalized:
            raise ValueError("Data de nascimento inválida")
        return normalized

class BulkDeletePayload(BaseModel):
    contact_ids: List[int]

class DeleteAllPayload(BaseModel):
    campaign_id: Optional[int] = None

class TransferPayload(BaseModel):
    contact_ids: List[int]
    target_campaign_id: int

@router.get("/")
def get_contacts(
    campaign_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    parsed_camp_id = None
    if campaign_id and str(campaign_id).strip():
        try:
            parsed_camp_id = int(str(campaign_id).strip())
        except ValueError:
            parsed_camp_id = None

    result = list_contacts(db, campaign_id=parsed_camp_id, status=status, search=search, skip=skip, limit=limit)
    stats = get_dashboard_stats(db, campaign_id=parsed_camp_id)
    return {
        "status": "success",
        "total": result["total"],
        "skip": skip,
        "limit": limit,
        "stats": stats,
        "contacts": result["contacts"]
    }

@router.get("/{contact_id}")
def get_contact(contact_id: int, db: Session = Depends(get_db)):
    contact = get_contact_by_id(db, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contato não encontrado.")
    return {"status": "success", "contact": contact}

@router.put("/{contact_id}")
def edit_contact(contact_id: int, payload: ContactUpdate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_unset=True)
    try:
        contact = update_contact(db, contact_id, data)
    except ContactQueryInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not contact:
        raise HTTPException(status_code=404, detail="Contato não encontrado.")
    return {"status": "success", "contact": contact}

@router.post("/transfer")
def transfer_leads_endpoint(payload: TransferPayload, db: Session = Depends(get_db)):
    if not payload.contact_ids:
        raise HTTPException(status_code=400, detail="Nenhum ID de contato fornecido.")
    
    updated = transfer_contacts_to_campaign(db, payload.contact_ids, payload.target_campaign_id)
    return {
        "status": "success",
        "message": f"{updated} lead(s) transferida(s) com sucesso.",
        "transferred_count": updated
    }

@router.post("/delete-all")
@router.delete("/delete-all")
def delete_all_leads_endpoint(payload: DeleteAllPayload = DeleteAllPayload(), db: Session = Depends(get_db)):
    """
    Exclui TODAS as leads do banco de dados (suporta POST e DELETE).
    """
    deleted = delete_all_contacts(db, campaign_id=payload.campaign_id)
    stats = get_dashboard_stats(db, campaign_id=payload.campaign_id)
    return {
        "status": "success",
        "message": f"Todas as {deleted} leads foram excluídas com sucesso.",
        "deleted_count": deleted,
        "stats": stats
    }

@router.post("/bulk-delete")
def bulk_remove_contacts(payload: BulkDeletePayload, db: Session = Depends(get_db)):
    if not payload.contact_ids:
        raise HTTPException(status_code=400, detail="Nenhum ID fornecido.")
    deleted = bulk_delete_contacts(db, payload.contact_ids)
    return {"status": "success", "message": f"{deleted} contatos removidos."}

@router.post("/bulk-status")
def bulk_update_status_endpoint(payload: BulkStatusPayload, db: Session = Depends(get_db)):
    if not payload.contact_ids:
        raise HTTPException(status_code=400, detail="Nenhum ID fornecido.")
    updated = bulk_update_contacts_status(db, payload.contact_ids, payload.credline_status)
    return {"status": "success", "message": f"Status de {updated} contatos alterado para '{payload.credline_status.upper()}'.", "updated_count": updated}

@router.delete("/{contact_id}")
def remove_contact(contact_id: int, db: Session = Depends(get_db)):
    ok = delete_contact(db, contact_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Contato não encontrado.")
    return {"status": "success", "message": "Contato deletado com sucesso."}
