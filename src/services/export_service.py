"""
export_service.py - Exportação de Contatos do Banco de Dados para Excel (.xlsx) / CSV
"""

import io
from typing import Optional, List
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from sqlalchemy.orm import Session
from src.database.models import Contact

HEADERS = [
    "ID", "Data Cadastro", "Campanha", "Telefone", "Nome WhatsApp", "Nome Contato", "Nome Limpo",
    "CPF", "Nome Mind7", "Status Itaú", "Valor Financiado", "Observação", "CEP", "Número", "Data de Nascimento", "Data Processamento",
    "Status Enriquecimento", "Observação Enriquecimento", "Status C6", "Valor Financiado C6", "Observação C6", "Data Consulta C6"
]

def export_contacts_to_excel(db: Session, campaign_id: Optional[int] = None, status: Optional[str] = None) -> bytes:
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

    contacts: List[Contact] = query.order_by(Contact.id.asc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Contatos Qualificados"

    ws.append(HEADERS)

    # Estilo Cabeçalho (Indigo #4F46E5)
    fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    font = Font(color="FFFFFF", bold=True, size=11)
    alignment = Alignment(horizontal="center", vertical="center")
    border = Border(bottom=Side(style="thin", color="312E81"))

    for col_num in range(1, len(HEADERS) + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.fill = fill
        cell.font = font
        cell.alignment = alignment
        cell.border = border

    for c in contacts:
        ws.append([
            c.id,
            c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else "",
            c.campaign.name if c.campaign else "",
            c.phone,
            c.whatsapp_name or "",
            c.contact_name or "",
            c.clean_name or "",
            c.cpf or "",
            c.mind7_name or "",
            c.credline_status or "PENDENTE",
            c.financed_value or "",
            c.observation or "",
            c.cep or "",
            c.house_number or "",
            c.birth_date or "",
            c.processed_at.strftime("%Y-%m-%d %H:%M:%S") if c.processed_at else "",
            c.enrichment_status or "PENDENTE", c.enrichment_observation or "",
            c.c6_status or "PENDENTE", c.c6_financed_value or "", c.c6_observation or "",
            c.c6_processed_at.strftime("%Y-%m-%d %H:%M:%S") if c.c6_processed_at else ""
        ])

    # Formatar células de status com cores
    for row_idx in range(2, ws.max_row + 1):
        status_cell = ws.cell(row=row_idx, column=10)
        val = str(status_cell.value or "").upper()
        if val == "NAO CORRENTISTA":
            status_cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
            status_cell.font = Font(color="006100", bold=True)
        elif val == "CORRENTISTA":
            status_cell.fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
            status_cell.font = Font(color="002060", bold=True)
        elif val in ("REPROVADO", "CONSULTADO"):
            status_cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
            status_cell.font = Font(color="9C0006")
        elif val == "SEM DADOS":
            status_cell.fill = PatternFill(start_color="E7E7E7", end_color="E7E7E7", fill_type="solid")
            status_cell.font = Font(color="595959")
        status_cell.alignment = Alignment(horizontal="center")

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()
