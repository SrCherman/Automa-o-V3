#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comparador de Planilhas de Contatos WhatsApp
---------------------------------------------
Compara planilhas exportadas do WhatsApp com a base de contatos limpos,
aplica filtros estritos em nomes/apelidos, atualiza a base limpa e anexa
os contatos inéditos na pipeline de crédito.

Recursos de Segurança e Qualidade:
  - Backups automáticos com timestamp antes de qualquer alteração
  - Gravação atômica via arquivo temporário (.tmp) + os.replace
  - Verificação de arquivo travado/aberto no Excel
  - Log detalhado de execução (comparacao.log)
  - Suporte ao modo simulação (--dry-run)
"""

import sys
import os
import re
import argparse
import datetime
import shutil
import unicodedata
from pathlib import Path
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

# Caminhos padrão do projeto
BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

DEFAULT_INPUT_FILE = BASE_DIR / "WhatsApp 2026-8-5 12-43-18.xlsx"
DEFAULT_LIMPOS_FILE = BASE_DIR / "Contatos_Limpos_WhatsApp.xlsx"
DEFAULT_PIPELINE_FILE = ROOT_DIR / "credit_pipeline" / "contatos_whatsapp.xlsx"
LOG_FILE = BASE_DIR / "comparacao.log"

# Standard pipeline headers
PIPELINE_HEADERS = [
    'Data Recebida',
    'Nome WhatsApp',
    'Telefone',
    'CPF',
    'Nome Mind7',
    'Status Itau',
    'Observacao',
    'CEP',
    'Numero',
    'Idade',
    'Valor Financiado'
]

# Regras de Expressões Regulares para Filtros
EXACT_PHRASE_PATTERNS = [
    r'\bdeus\s+seja\s+louvado\b',
    r'\bos\s+mist[eé]rio\s+de\s+deus\b',
    r'\bdeus\s+est[aá]\s+no\s+controle\b',
    r'\bminha\s+casa\s+minhas\s+regras\b',
    r'\bpronto\s+pra\s+guerra\b',
    r'\bfilho\s+do\s+rei\b',
    r'\bfilho\s+de\s+deus\b',
    r'\beu\s+sei,\s*como\s+voc[eê]\s+sabe\b',
    r'\bmeu\s+filho,?\s+minha\s+vida\b',
    r'\bdeus\s+me\s+guia\b',
    r'\bf[eé]\s+inabal[aá]vel\b',
    r'\bviver\s+intensamente\b',
    r'\bdeus\s+[eé]\s+bom\b',
    r'\bblindada\s+por\s+deus\b',
    r'\bt[oô]\s+na\s+area\b',
    r'\bfam[ií]lia\s+a\s+base\s+de\s+tudo\b',
    r'\beo\s+velho\s+bode\b',
    r'\bdeus\s+[eé]\s+fiel\b',
    r'\bcai\s+corintia\b',
    r'\bdeus\s+[eé]\s+mais\b',
    r'\bpai\s+te\s+ama\b',
    r'\bjesus\s+te\s+ama\b',
]

NON_NAME_TERMS = [
    r'^oi$',
    r'^uber',
    r'^pedreiro',
    r'^motoboy',
    r'^servi[cç]o\s+log[ií]stica',
    r'^da_150_',
    r'^\s*almar\s+vix'
]


def log_message(msg: str, console: bool = True):
    """Registra uma mensagem no log e opcionalmente no console."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    if console:
        print(msg)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception as e:
        if console:
            print(f"[AVISO] Falha ao escrever no log: {e}")


def normalizar_telefone(telefone: str) -> str:
    """Remove caracteres não numéricos e exclui o DDI 55 quando presente."""
    if not telefone:
        return ""
    digits = re.sub(r"\D", "", str(telefone).strip())
    if digits.startswith("55") and len(digits) > 11:
        digits = digits[2:]
    if len(digits) > 11:
        digits = digits[-11:]
    return digits


def validar_nome_contato(nome_raw: str) -> tuple[bool, str]:
    """
    Aplica as 4 regras de validação para filtrar contatos inválidos.
    Retorna (True, 'OK') se for válido, ou (False, motivo) se for rejeitado.
    """
    if not nome_raw:
        return False, "Regra 1: Nome Vazio/Nulo"

    nome = str(nome_raw).strip()
    nome_lower = nome.lower()

    # 1. Possuem o nome N/A ou variações de nulo
    if nome_lower in {'n/a', 'na', 'null', 'none', 'desconhecido', 'unknown', 'undefined'}:
        return False, "Regra 1: Nome N/A ou desconhecido"

    # Extrai letras Unicode (Latinas/Acentuadas)
    letras = [ch for ch in nome if unicodedata.category(ch).startswith('L')]

    # 2. Possuem nomes compostos apenas por emojis, caracteres especiais ou números
    if not letras:
        return False, "Regra 2: Composto apenas por emojis, caracteres especiais ou números"

    # 3. Contatos cujo nome sejam frases apenas (ex: Deus é Fiel, Cai corintia, Deus no controle)
    for pat in EXACT_PHRASE_PATTERNS:
        if re.search(pat, nome_lower):
            return False, f"Regra 3: Frase/Slogan detectado ({pat})"

    # Frases terminadas em pontuação com 3+ palavras
    words = nome.split()
    if len(words) >= 3 and nome.strip()[-1] in '.!?':
        if any(w.lower() in {'deus', 'guia', 'sempre', 'controle', 'vida', 'abençoado', 'abencoado'} for w in words):
            return False, "Regra 3: Frase declarativa com pontuação"

    # 4. Contatos cujo apelido não remeta a abreviação de um nome (ex: apenas 1 letra ou termo genérico)
    if len(letras) < 2:
        return False, "Regra 4: Menos de 2 letras (não é nome/abreviação válida)"

    for term in NON_NAME_TERMS:
        if re.search(term, nome_lower):
            return False, f"Regra 4: Termo genérico/ocupação ({term})"

    return True, "OK"


def criar_backup(file_path: Path) -> Path | None:
    """Cria um backup timestamped na pasta backups/ do mesmo diretório."""
    if not file_path.exists():
        return None

    backup_dir = file_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{file_path.stem}_{timestamp}{file_path.suffix}"

    try:
        shutil.copy2(file_path, backup_path)
        log_message(f"[BACKUP] Backup criado com sucesso: {backup_path.name}")
        return backup_path
    except Exception as e:
        log_message(f"[ERRO] Falha ao criar backup de {file_path.name}: {e}")
        raise e


def salvar_planilha_segura(wb: Workbook, target_path: Path):
    """Salva a planilha via gravação atômica (.tmp + os.replace) para evitar corrupção."""
    temp_path = target_path.with_suffix(".xlsx.tmp")

    if temp_path.exists():
        try:
            temp_path.unlink()
        except OSError:
            pass

    wb.save(temp_path)
    wb.close()

    # Tentativa de substituição atômica com retries caso esteja temporariamente preso
    try:
        os.replace(temp_path, target_path)
        log_message(f"[SALVO] Arquivo atualizado com segurança: {target_path.name}")
    except PermissionError:
        log_message(f"[ERRO CRÍTICO] O arquivo '{target_path.name}' está aberto em outro programa (ex: Microsoft Excel). Por favor, feche o arquivo e execute novamente.")
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise PermissionError(f"Arquivo preso: {target_path.name}")


def processar_comparacao(
    input_file: Path,
    limpos_file: Path,
    pipeline_file: Path,
    dry_run: bool = False
):
    """Executa o fluxo completo de comparação, filtragem e atualização."""
    log_message("=" * 60)
    log_message(f"Iniciando Comparador de Planilhas WhatsApp {'[MODO SIMULAÇÃO / DRY-RUN]' if dry_run else ''}")
    log_message("=" * 60)

    # 1. Carregar planilha de contatos limpos existente para deduplicação
    if not limpos_file.exists():
        log_message(f"[ERRO] Arquivo de contatos limpos não encontrado: {limpos_file}")
        sys.exit(1)

    wb_limpos = load_workbook(limpos_file)
    ws_limpos = wb_limpos.active
    rows_limpos = list(ws_limpos.iter_rows(values_only=True))

    contatos_limpos_existentes = []
    telefones_existentes = set()

    for r in rows_limpos[1:]:
        if not r or len(r) < 2:
            continue
        nome = str(r[0] or "").strip()
        tel_norm = normalizar_telefone(r[1])
        if tel_norm:
            telefones_existentes.add(tel_norm)
            contatos_limpos_existentes.append((nome, tel_norm))

    log_message(f"[BASE] Contatos limpos existentes carregados: {len(telefones_existentes)} números únicos.")

    # 2. Carregar arquivo exportado do WhatsApp
    if not input_file.exists():
        log_message(f"[ERRO] Arquivo de entrada do WhatsApp não encontrado: {input_file}")
        sys.exit(1)

    wb_wa = load_workbook(input_file)
    ws_wa = wb_wa.active
    rows_wa = list(ws_wa.iter_rows(values_only=True))

    if not rows_wa:
        log_message("[AVISO] O arquivo de entrada está vazio.")
        return

    data_rows_wa = rows_wa[1:]
    log_message(f"[ENTRADA] Total de registros no arquivo WhatsApp exportado: {len(data_rows_wa)}")

    # 3. Comparar e identificar contatos inéditos e aplicar filtros
    novos_ineditos_raw = 0
    duplicados_existentes = 0
    aceitos = []
    rejeitados = []

    for idx, r in enumerate(data_rows_wa, start=1):
        if not r:
            continue

        phone_raw = r[0] if len(r) > 0 else ""
        cname = str(r[1] or "").strip() if len(r) > 1 else ""
        wname = str(r[2] or "").strip() if len(r) > 2 else ""

        # Define o nome a ser avaliado (se Contact Name for N/A ou vazio, usa WhatsApp Name)
        nome_para_avaliar = wname if (cname in {'', 'N/A'} or not cname) else cname

        tel_norm = normalizar_telefone(phone_raw)
        if not tel_norm:
            rejeitados.append((phone_raw, nome_para_avaliar, "Regra 2: Telefone inválido/vazio"))
            continue

        # Verifica se é inédito em relação aos telefones já conhecidos
        if tel_norm in telefones_existentes:
            duplicados_existentes += 1
            continue

        novos_ineditos_raw += 1

        # Aplica os filtros de nome
        valido, motivo = validar_nome_contato(nome_para_avaliar)
        if valido:
            aceitos.append((nome_para_avaliar, tel_norm))
            # Adiciona ao set para evitar duplicatas dentro do próprio lote
            telefones_existentes.add(tel_norm)
        else:
            rejeitados.append((tel_norm, nome_para_avaliar, motivo))

    log_message("-" * 60)
    log_message("RESUMO DO PROCESSAMENTO DE COMPARACAO:")
    log_message(f"  - Contatos inéditos brutos identificados: {novos_ineditos_raw}")
    log_message(f"  - Contatos já existentes (ignorados): {duplicados_existentes}")
    log_message(f"  - Contatos ACEITOS (Passaram nos filtros): {len(aceitos)}")
    log_message(f"  - Contatos REJEITADOS pelos filtros: {len(rejeitados)}")
    log_message("-" * 60)

    # Detalhamento de Rejeitados por Categoria
    categorias_rejeicao = {}
    for _, _, motivo in rejeitados:
        cat = motivo.split(":")[0] if ":" in motivo else motivo
        categorias_rejeicao[cat] = categorias_rejeicao.get(cat, 0) + 1

    log_message("Detalhamento dos Filtros de Rejeição:")
    for cat, count in categorias_rejeicao.items():
        log_message(f"  * {cat}: {count} contatos")
    log_message("-" * 60)

    if dry_run:
        log_message("[SIMULAÇÃO] Modo --dry-run ativado. Nenhum arquivo foi modificado.")
        return

    # 4. Atualizar `Contatos_Limpos_WhatsApp.xlsx` (Sobrescrever com os contatos limpos filtrados)
    log_message("[ESCRITA] Atualizando planilha Contatos_Limpos_WhatsApp.xlsx...")
    criar_backup(limpos_file)

    wb_novo_limpos = Workbook()
    ws_novo_limpos = wb_novo_limpos.active
    ws_novo_limpos.title = "Contatos"

    # Escrever cabeçalho
    ws_novo_limpos.append(['Nome do contato', 'Número'])

    # Estilo do cabeçalho
    header_fill = PatternFill(start_color="002060", end_color="002060", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for col in range(1, 3):
        cell = ws_novo_limpos.cell(row=1, column=col)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Escrever todos os contatos aceitos
    for nome, tel in aceitos:
        ws_novo_limpos.append([nome, tel])

    ws_novo_limpos.column_dimensions['A'].width = 30
    ws_novo_limpos.column_dimensions['B'].width = 20

    salvar_planilha_segura(wb_novo_limpos, limpos_file)

    # 5. Anexar contatos inéditos na planilha `credit_pipeline/contatos_whatsapp.xlsx`
    if pipeline_file.exists():
        log_message(f"[ESCRITA] Anexando {len(aceitos)} novos contatos inéditos em credit_pipeline/contatos_whatsapp.xlsx...")
        criar_backup(pipeline_file)

        wb_pipe = load_workbook(pipeline_file)
        ws_pipe = wb_pipe.active

        data_atual = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for nome, tel in aceitos:
            ws_pipe.append([
                data_atual,   # A - Data Recebida
                nome,         # B - Nome WhatsApp
                tel,          # C - Telefone
                '',           # D - CPF
                '',           # E - Nome Mind7
                'PENDENTE',   # F - Status Itau
                '',           # G - Observacao
                '',           # H - CEP
                '',           # I - Numero
                '',           # J - Idade
                ''            # K - Valor Financiado
            ])

            # Formata célula de status como PENDENTE (Amarelo)
            r_idx = ws_pipe.max_row
            cell_st = ws_pipe.cell(row=r_idx, column=6)
            cell_st.fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
            cell_st.alignment = Alignment(horizontal="center")

        salvar_planilha_segura(wb_pipe, pipeline_file)
    else:
        log_message(f"[AVISO] Planilha de pipeline não encontrada em {pipeline_file}. Pulando anexo na pipeline.")

    log_message("=" * 60)
    log_message("[SUCESSO] Processamento concluído com sucesso!")
    log_message("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Comparador de Planilhas WhatsApp e Filtro de Contatos")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_FILE, help="Caminho da planilha exportada do WhatsApp")
    parser.add_argument("--limpos", type=Path, default=DEFAULT_LIMPOS_FILE, help="Caminho da planilha de contatos limpos")
    parser.add_argument("--pipeline", type=Path, default=DEFAULT_PIPELINE_FILE, help="Caminho da planilha da pipeline de crédito")
    parser.add_argument("--dry-run", action="store_true", help="Executa apenas a simulação sem alterar nenhum arquivo")

    args = parser.parse_args()

    try:
        processar_comparacao(
            input_file=args.input,
            limpos_file=args.limpos,
            pipeline_file=args.pipeline,
            dry_run=args.dry_run
        )
    except Exception as e:
        log_message(f"[ERRO FATAL] Ocorreu uma falha durante a execução: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
