"""
crud.py - Operações de Banco de Dados (CRUD, Estatísticas, Transferência e Exclusão Segura)
"""

from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_
from datetime import datetime
from src.database.models import Campaign, Contact, AuditLog

# --- CAMPAIGNS ---

def get_or_create_default_campaign(db: Session) -> Campaign:
    campaign = db.query(Campaign).filter(Campaign.name == "Campanha Padrão").first()
    if not campaign:
        campaign = Campaign(name="Campanha Padrão", description="Campanha padrão do sistema")
        db.add(campaign)
        db.commit()
        db.refresh(campaign)
    return campaign

def create_campaign(db: Session, name: str, description: str = "") -> Campaign:
    campaign = Campaign(name=name, description=description)
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign

def list_campaigns(db: Session) -> List[Campaign]:
    return db.query(Campaign).order_by(Campaign.created_at.desc()).all()

def delete_campaign(db: Session, campaign_id: int) -> bool:
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if campaign:
        _protect_contacts_for_deletion(db, db.query(Contact).filter(Contact.campaign_id == campaign_id))
        # Excluir logs de auditoria antes da campanha
        contact_ids = [c.id for c in campaign.contacts]
        if contact_ids:
            db.query(AuditLog).filter(AuditLog.contact_id.in_(contact_ids)).delete(synchronize_session=False)
        db.delete(campaign)
        db.commit()
        return True
    return False

# --- CONTACTS ---

class ContactQueryInProgress(ValueError):
    pass


def _protect_contacts_for_deletion(db: Session, targets):
    available = targets.filter(
        func.coalesce(Contact.credline_status, "") != "PROCESSANDO",
        func.coalesce(Contact.enrichment_status, "") != "PROCESSANDO",
        ~func.coalesce(Contact.c6_status, "").in_(("PROCESSANDO", "AGUARDANDO RESULTADO")))
    if available.update({Contact.id: Contact.id}, synchronize_session=False) != targets.count():
        db.rollback()
        raise ContactQueryInProgress("Aguarde as consultas terminarem antes de excluir os contatos.")


def get_contact_by_id(db: Session, contact_id: int) -> Optional[Contact]:
    return db.query(Contact).filter(Contact.id == contact_id).first()

def list_contacts(
    db: Session,
    campaign_id: Optional[int] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 500
) -> Dict[str, Any]:
    query = db.query(Contact)

    if campaign_id:
        query = query.filter(Contact.campaign_id == campaign_id)

    if status and status.upper() != "TODOS":
        if status.upper() == "APROVADOS":
            query = query.filter(Contact.credline_status.in_(["NAO CORRENTISTA", "CORRENTISTA"]))
        elif status.upper() == "REPROVADOS":
            query = query.filter(Contact.credline_status.in_(["CONSULTADO", "REPROVADO"]))
        else:
            query = query.filter(Contact.credline_status == status.upper())

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Contact.clean_name.ilike(term),
                Contact.phone.ilike(term),
                Contact.cpf.ilike(term),
                Contact.mind7_name.ilike(term),
                Contact.observation.ilike(term)
            )
        )

    total = query.count()
    contacts = query.order_by(Contact.id.desc()).offset(skip).limit(limit).all()

    return {
        "total": total,
        "contacts": contacts
    }

def update_contact(db: Session, contact_id: int, data: Dict[str, Any]) -> Optional[Contact]:
    contact = get_contact_by_id(db, contact_id)
    if not contact:
        return None

    identity_changed = any(key in data and data[key] != getattr(contact, key) for key in ("cpf", "birth_date", "phone")) or data.get("retry_c6")
    status_filters = [
        or_(~func.coalesce(getattr(Contact, key), "").in_(active), getattr(Contact, key) == data[key])
        for key, active in (("credline_status", ("PROCESSANDO",)),
                            ("enrichment_status", ("PROCESSANDO",)),
                            ("c6_status", ("PROCESSANDO", "AGUARDANDO RESULTADO")))
        if data.get(key) is not None
    ]
    if identity_changed or status_filters:
        available = db.query(Contact).filter(Contact.id == contact_id, *status_filters)
        if identity_changed:
            available = available.filter(
                func.coalesce(Contact.credline_status, "") != "PROCESSANDO",
                func.coalesce(Contact.enrichment_status, "") != "PROCESSANDO",
                ~func.coalesce(Contact.c6_status, "").in_(("PROCESSANDO", "AGUARDANDO RESULTADO")))
            if "cpf" in data and data["cpf"] != contact.cpf and not data.get("retry_c6"):
                available = available.filter(func.coalesce(Contact.c6_status, "") != "CONFERIR RESULTADO")
        # A escrita mantém a linha protegida até o commit da edição.
        if not available.update({Contact.id: Contact.id}, synchronize_session=False):
            db.rollback()
            raise ContactQueryInProgress("Aguarde a consulta terminar. Confira o resultado C6 e confirme nova tentativa antes de trocar o CPF.")
        db.refresh(contact)

    import re
    changed_cpf = "cpf" in data and re.sub(r"\D", "", data["cpf"] or "") != re.sub(r"\D", "", contact.cpf or "")
    changed_birth_date = "birth_date" in data and data["birth_date"] != contact.birth_date
    if changed_cpf:
        for key in ("mind7_name", "birth_date", "cep", "house_number", "age", "financed_value",
                    "c6_financed_value", "processed_at", "enriched_at", "c6_processed_at"):
            setattr(contact, key, None)
            data.pop(key, None)
        contact.enrichment_status = contact.c6_status = "PENDENTE"
        contact.credline_status = "CPF COLETADO" if data["cpf"] else "PENDENTE"
        contact.enrichment_attempts = contact.c6_attempts = 0
        contact.enrichment_observation = contact.c6_observation = None
        data.pop("credline_status", None)
    if data.get("enrichment_status") == "PENDENTE":
        contact.enrichment_attempts = 0
    for key, value in data.items():
        if hasattr(contact, key) and (value is not None or key == "birth_date"):
            setattr(contact, key, value)
    if "birth_date" in data and contact.birth_date and contact.enrichment_status in ("INCOMPLETO", "SEM DADOS"):
        contact.enrichment_status = "CONCLUIDO"
    if contact.c6_status == "AGUARDANDO DADOS" and "birth_date" in data:
        contact.c6_status = "PENDENTE"
        contact.c6_attempts = 0
    if changed_birth_date and not changed_cpf and contact.c6_status in ("APROVADO", "FORA DA POLITICA"):
        contact.c6_status = "CONFERIR RESULTADO"
        contact.c6_observation = "Nascimento alterado após consulta; confira o resultado anterior no C6"
    if data.get("retry_c6"):
        contact.c6_status = "PENDENTE"
        contact.c6_attempts = 0
        contact.c6_financed_value = None

    db.commit()
    db.refresh(contact)
    return contact

def delete_contact(db: Session, contact_id: int) -> bool:
    contact = get_contact_by_id(db, contact_id)
    if contact:
        _protect_contacts_for_deletion(db, db.query(Contact).filter(Contact.id == contact_id))
        db.query(AuditLog).filter(AuditLog.contact_id == contact_id).delete(synchronize_session=False)
        db.delete(contact)
        db.commit()
        return True
    return False

def bulk_delete_contacts(db: Session, contact_ids: List[int]) -> int:
    """
    Exclui em lote uma lista de IDs de contatos garantindo a remoção limpa dos logs.
    """
    if not contact_ids:
        return 0
    _protect_contacts_for_deletion(db, db.query(Contact).filter(Contact.id.in_(contact_ids)))
    
    # Clean audit logs first
    db.query(AuditLog).filter(AuditLog.contact_id.in_(contact_ids)).delete(synchronize_session=False)
    deleted = db.query(Contact).filter(Contact.id.in_(contact_ids)).delete(synchronize_session=False)
    db.commit()
    return deleted

def delete_all_contacts(db: Session, campaign_id: Optional[int] = None) -> int:
    """
    Exclui TODAS as leads do banco de dados (geral ou filtrado por campanha).
    Usa subquery para evitar carregar todos os IDs na memória.
    """
    query = db.query(Contact)
    if campaign_id:
        query = query.filter(Contact.campaign_id == campaign_id)
    _protect_contacts_for_deletion(db, query)
    
    target_subq = query.with_entities(Contact.id).subquery()
    db.query(AuditLog).filter(AuditLog.contact_id.in_(db.query(target_subq))).delete(synchronize_session=False)
    deleted = query.delete(synchronize_session=False)
    db.commit()
    return deleted

def transfer_contacts_to_campaign(db: Session, contact_ids: List[int], target_campaign_id: int) -> int:
    if not contact_ids:
        return 0
    targets = db.query(Contact).filter(Contact.id.in_(contact_ids))
    available = targets.filter(
        func.coalesce(Contact.credline_status, "") != "PROCESSANDO",
        func.coalesce(Contact.enrichment_status, "") != "PROCESSANDO",
        ~func.coalesce(Contact.c6_status, "").in_(("PROCESSANDO", "AGUARDANDO RESULTADO")))
    updated = available.update(
        {Contact.campaign_id: target_campaign_id}, synchronize_session=False
    )
    if updated != targets.count():
        db.rollback()
        raise ContactQueryInProgress("Aguarde a consulta terminar antes de transferir os contatos selecionados.")
    db.commit()
    return updated

def bulk_update_contacts_status(db: Session, contact_ids: List[int], status: str) -> int:
    if not contact_ids:
        return 0
    status = status.upper()
    targets = db.query(Contact).filter(Contact.id.in_(contact_ids))
    available = targets
    if status != "PROCESSANDO":
        available = available.filter(func.coalesce(Contact.credline_status, "") != "PROCESSANDO")
    updated = available.update({Contact.credline_status: status}, synchronize_session=False)
    if updated != targets.count():
        db.rollback()
        raise ContactQueryInProgress("Aguarde a consulta terminar antes de alterar o status dos contatos selecionados.")
    db.commit()
    return updated


# --- STATISTICS ---

def get_dashboard_stats(db: Session, campaign_id: Optional[int] = None) -> Dict[str, Any]:
    query = db.query(Contact)
    if campaign_id:
        query = query.filter(Contact.campaign_id == campaign_id)

    total = query.count()
    if total == 0:
        return {
            "total": 0, "processados": 0, "pendentes": 0, "cpfs_coletados": 0,
            "aprovados": 0, "nao_correntistas": 0, "correntistas": 0,
            "reprovados": 0, "sem_dados": 0, "erros": 0, "duplicados": 0, "taxa_aprovacao": 0.0,
            "birth_dates": 0, "c6_finished": 0, "c6_review": 0
        }

    # QUERY ÚNICA GROUP BY PARA ALTA PERFORMANCE
    status_counts = dict(
        query.with_entities(
            func.coalesce(Contact.credline_status, "PENDENTE"),
            func.count(Contact.id)
        ).group_by(func.coalesce(Contact.credline_status, "PENDENTE")).all()
    )

    nao_correntistas = status_counts.get("NAO CORRENTISTA", 0)
    correntistas = status_counts.get("CORRENTISTA", 0)
    aprovados = nao_correntistas + correntistas
    reprovados = status_counts.get("REPROVADO", 0) + status_counts.get("CONSULTADO", 0)
    
    pendente_keys = ["PENDENTE", "CPF COLETADO", "PENDENTE ITAU", "", "PROCESSANDO"]
    pendentes = sum(status_counts.get(k, 0) for k in pendente_keys)

    sem_dados = status_counts.get("SEM DADOS", 0)
    erros = status_counts.get("ERRO", 0)
    duplicados = status_counts.get("DUPLICADO", 0)

    cpfs_coletados_subq = query.filter(and_(Contact.cpf.isnot(None), Contact.cpf != ""))
    cpfs_coletados = cpfs_coletados_subq.with_entities(func.count(Contact.id)).scalar()
    birth_dates = cpfs_coletados_subq.filter(Contact.birth_date.isnot(None), Contact.birth_date != "").count()
    c6_counts = dict(query.with_entities(Contact.c6_status, func.count(Contact.id)).group_by(Contact.c6_status).all())
    c6_finished = sum(c6_counts.get(status, 0) for status in ("APROVADO", "FORA DA POLITICA", "DADOS INVALIDOS"))
    processados = total - pendentes
    taxa_aprovacao = round((aprovados / processados * 100), 1) if processados > 0 else 0.0

    return {
        "total": total,
        "processados": processados,
        "pendentes": pendentes,
        "cpfs_coletados": cpfs_coletados,
        "aprovados": aprovados,
        "nao_correntistas": nao_correntistas,
        "correntistas": correntistas,
        "reprovados": reprovados,
        "sem_dados": sem_dados,
        "erros": erros,
        "duplicados": duplicados,
        "taxa_aprovacao": taxa_aprovacao,
        "birth_dates": birth_dates, "c6_finished": c6_finished,
        "c6_review": c6_counts.get("CONFERIR RESULTADO", 0)
    }


# --- RESET DATABASE ---

def reset_database(db: Session, campaign_id: Optional[int] = None) -> Dict[str, Any]:
    if campaign_id:
        _protect_contacts_for_deletion(db, db.query(Contact).filter(Contact.campaign_id == campaign_id))
        target_subq = db.query(Contact.id).filter(Contact.campaign_id == campaign_id).subquery()
        db.query(AuditLog).filter(AuditLog.contact_id.in_(db.query(target_subq))).delete(synchronize_session=False)
        count = db.query(Contact).filter(Contact.campaign_id == campaign_id).delete(synchronize_session=False)
        db.commit()
        return {"status": "success", "message": f"Removidos {count} contatos da campanha {campaign_id}."}
    else:
        _protect_contacts_for_deletion(db, db.query(Contact))
        audit_count = db.query(AuditLog).delete(synchronize_session=False)
        contacts_count = db.query(Contact).delete(synchronize_session=False)
        db.commit()
        return {"status": "success", "message": f"Banco de dados zerado com sucesso ({contacts_count} contatos e {audit_count} logs removidos)."}
