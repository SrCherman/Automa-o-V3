"""
Modulo de gerenciamento da planilha Excel central.
Responsavel por:
  - Adicionar contatos novos (nome + telefone) vindos do WhatsApp
  - Ler contatos pendentes para processamento
  - Atualizar o status de cada contato apos consulta Mind7/Itau
"""

import os
import re
import time
from datetime import datetime
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
import threading

FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'contatos_whatsapp.xlsx'
)
TEMP_FILE_PATH = FILE_PATH + '.tmp'

_lock = threading.Lock()

# Colunas: A=Data | B=Nome WhatsApp | C=Telefone | D=CPF | E=Nome Mind7 | F=Status Itau | G=Observacao

HEADERS = [
    'Data Recebida',
    'Nome WhatsApp',
    'Telefone',
    'CPF',
    'Nome Mind7',
    'Status Itau',
    'Observacao',
    'CEP',
    'Numero',
    'Data de Nascimento',
    'Valor Financiado'
]

NOISE_NAME_RE = re.compile(
    r"^(?:\d+\s+)?mensagens?\s+n[ãa]o\s+lidas?$|^online$|^digitando$|^agora$|^ontem$|^visto por ultimo$|^last seen$",
    re.I,
)


def nome_valido(nome: str) -> bool:
    text = " ".join(str(nome or "").strip().split())
    if not text or len(text) < 2:
        return False
    if NOISE_NAME_RE.fullmatch(text.lower()):
        return False
    if "mensagem" in text.lower() and "lida" in text.lower():
        return False
    return True


def _criar_planilha():
    """Cria a planilha com cabecalhos formatados."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Contatos"
    ws.append(HEADERS)

    # Estilo do cabecalho
    fill = PatternFill(start_color="002060", end_color="002060", fill_type="solid")
    font = Font(color="FFFFFF", bold=True, size=11)
    alignment = Alignment(horizontal="center", vertical="center")
    border = Border(
        bottom=Side(style="thin", color="000000")
    )

    for col in range(1, len(HEADERS) + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = fill
        cell.font = font
        cell.alignment = alignment
        cell.border = border

    # Larguras
    ws.column_dimensions['A'].width = 20
    ws.column_dimensions['B'].width = 30
    ws.column_dimensions['C'].width = 16
    ws.column_dimensions['D'].width = 16
    ws.column_dimensions['E'].width = 35
    ws.column_dimensions['F'].width = 22
    ws.column_dimensions['G'].width = 30
    ws.column_dimensions['H'].width = 14
    ws.column_dimensions['I'].width = 12
    ws.column_dimensions['J'].width = 18
    ws.column_dimensions['K'].width = 20

    _salvar_planilha(wb)
    return load_workbook(FILE_PATH)


def _salvar_planilha(wb):
    """Salva em arquivo temporario e troca no final para reduzir risco de XLSX parcial."""
    temp_xlsx = TEMP_FILE_PATH
    if os.path.exists(temp_xlsx):
        try:
            os.remove(temp_xlsx)
        except OSError:
            pass

    wb.save(temp_xlsx)
    try:
        wb.close()
    except Exception:
        pass

    last_error = None
    for tentativa in range(12):
        try:
            os.replace(temp_xlsx, FILE_PATH)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.75 * (tentativa + 1))

    backup = FILE_PATH + f".pendente_{int(time.time())}"
    try:
        os.replace(temp_xlsx, backup)
    except OSError:
        pass
    raise last_error


def _abrir_planilha():
    """Abre a planilha existente ou cria uma nova."""
    if os.path.exists(FILE_PATH):
        return load_workbook(FILE_PATH)
    return _criar_planilha()


def normalizar_telefone(telefone: str) -> str:
    """Mantem DDD + numero, removendo DDI 55 quando existir."""
    numero = re.sub(r"\D", "", str(telefone or ""))
    if numero.startswith("55") and len(numero) > 11:
        numero = numero[2:]
    if len(numero) > 11:
        numero = numero[-11:]
    return numero


def _telefone_existe_ws(ws, telefone: str) -> bool:
    telefone_norm = normalizar_telefone(telefone)
    for row in ws.iter_rows(min_row=2, min_col=3, max_col=3, values_only=True):
        if normalizar_telefone(row[0]) == telefone_norm:
            return True
    return False


def telefone_ja_existe(telefone: str) -> bool:
    """Verifica se o telefone ja esta na planilha para evitar duplicatas."""
    with _lock:
        wb = _abrir_planilha()
        ws = wb.active
        return _telefone_existe_ws(ws, telefone)


def adicionar_contato(nome: str, telefone: str):
    """
    Adiciona um novo contato na planilha com status pendente.
    Retorna True se adicionou, False se ja existia.
    """
    with _lock:
        wb = _abrir_planilha()
        ws = wb.active
        telefone = normalizar_telefone(telefone)
        nome = " ".join(str(nome or "Desconhecido").strip().split()) or "Desconhecido"

        if not nome_valido(nome):
            nome = "Desconhecido"

        if not telefone or len(telefone) < 10:
            return False

        if _telefone_existe_ws(ws, telefone):
            return False

        data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ws.append([
            data_atual,       # A - Data Recebida
            nome,             # B - Nome WhatsApp
            telefone,         # C - Telefone
            '',               # D - CPF (sera preenchido depois)
            '',               # E - Nome Mind7 (sera preenchido depois)
            'PENDENTE',       # F - Status Itau
            '',               # G - Observacao
            '',               # H - CEP
            '',               # I - Numero
            '',               # J - Ano Nascimento
            ''                # K - Valor Financiado
        ])

        # Formata a celula de status como amarelo
        row_num = ws.max_row
        cell_status = ws.cell(row=row_num, column=6)
        cell_status.fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
        cell_status.alignment = Alignment(horizontal="center")

        _salvar_planilha(wb)
        return True


def adicionar_contatos_em_lote(contatos: list) -> dict:
    """Adiciona contatos em lote mantendo a deduplicacao atomica."""
    adicionados = 0
    duplicados = 0
    invalidos = 0

    with _lock:
        wb = _abrir_planilha()
        ws = wb.active

        for contato in contatos:
            nome = " ".join(str(contato.get("nome") or contato.get("pushName") or "Desconhecido").strip().split())
            telefone = normalizar_telefone(contato.get("telefone") or contato.get("remoteJid") or "")

            if not nome_valido(nome):
                nome = "Desconhecido"

            if not telefone or len(telefone) < 10:
                invalidos += 1
                continue

            if _telefone_existe_ws(ws, telefone):
                duplicados += 1
                continue

            ws.append([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                nome or "Desconhecido",
                telefone,
                '',
                '',
                'PENDENTE',
                '',
                '',
                '',
                '',
                ''
            ])
            row_num = ws.max_row
            cell_status = ws.cell(row=row_num, column=6)
            cell_status.fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
            cell_status.alignment = Alignment(horizontal="center")
            adicionados += 1

        _salvar_planilha(wb)

    return {"adicionados": adicionados, "duplicados": duplicados, "invalidos": invalidos}


def listar_pendentes() -> list:
    """Retorna uma lista de dicts com os contatos que ainda estao PENDENTE.
    Linhas com nome + telefone e status vazio tambem entram na fila.
    """
    with _lock:
        wb = _abrir_planilha()
        ws = wb.active
        pendentes = []
        for row in ws.iter_rows(min_row=2, values_only=False):
            status_cell = row[5]  # Coluna F (index 5)
            status = str(status_cell.value or "").strip().upper()
            nome = str(row[1].value or "").strip()
            telefone = str(row[2].value or "").strip()

            if not nome or not telefone:
                continue

            if status in {"", "PENDENTE"}:
                pendentes.append({
                    'row_num': status_cell.row,
                    'nome_whatsapp': nome,
                    'telefone': telefone,
                })
        return pendentes


def atualizar_resultado(row_num: int, cpf: str, nome_mind7: str, status: str, observacao: str = ''):
    """
    Atualiza uma linha da planilha com o resultado da consulta.
    status pode ser: 'NAO CORRENTISTA', 'CORRENTISTA', 'ERRO', 'SEM DADOS'
    """
    with _lock:
        wb = _abrir_planilha()
        ws = wb.active

        ws.cell(row=row_num, column=4, value=cpf)            # D - CPF
        ws.cell(row=row_num, column=5, value=nome_mind7)     # E - Nome Mind7

        cell_status = ws.cell(row=row_num, column=6, value=status)
        cell_status.alignment = Alignment(horizontal="center")

        cell_obs = ws.cell(row=row_num, column=7, value=observacao)

        # Cores por status
        if status == 'NAO CORRENTISTA':
            cell_status.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
            cell_status.font = Font(color="006100", bold=True)
        elif status == 'CORRENTISTA':
            cell_status.fill = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
            cell_status.font = Font(color="002060", bold=True)
        elif status == 'SEM DADOS':
            cell_status.fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
            cell_status.font = Font(color="404040")
        elif status == 'ERRO':
            cell_status.fill = PatternFill(start_color="FCE4EC", end_color="FCE4EC", fill_type="solid")
            cell_status.font = Font(color="B71C1C")

        _salvar_planilha(wb)
        print(f"[PLANILHA] Linha {row_num} atualizada: {status} | CPF: {cpf} | {nome_mind7}")


def marcar_processando(row_num: int, observacao: str = ''):
    """Marca uma linha como em processamento sem sobrescrever CPF/Nome já existentes."""
    with _lock:
        wb = _abrir_planilha()
        ws = wb.active

        cell_status = ws.cell(row=row_num, column=6, value='PROCESSANDO')
        cell_status.alignment = Alignment(horizontal="center")
        cell_status.fill = PatternFill(start_color="D9EAF7", end_color="D9EAF7", fill_type="solid")
        cell_status.font = Font(color="1F4E78", bold=True)

        if observacao:
            ws.cell(row=row_num, column=7, value=observacao)

        _salvar_planilha(wb)
