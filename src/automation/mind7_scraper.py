"""
mind7_scraper.py - Automação Híbrida Adaptativa do Portal Mind7 (Métodos de Coleta e Matching do MODELO BASE)
"""

import re
import time
import logging
from datetime import date, datetime
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse
from src.config import MIND7_URL, MIND7_CPF_URL, MIND7_LOGIN_URL, MIND7_USER, MIND7_PASS
from src.automation.cloudflare_solver import check_and_solve_cloudflare
from src.ingestion.rules import calcular_score_nome, extrair_nome_real

log = logging.getLogger("mind7_scraper")

class Mind7Unavailable(RuntimeError):
    """Sessão ou verificação do portal exige atenção antes de continuar."""


def normalizar_nascimento(value: str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        value = value.isoformat()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            nascimento = datetime.strptime(value or "", fmt).date()
            hoje = date.today()
            if date(hoje.year - 120, 1, 1) <= nascimento <= hoje:
                return nascimento.isoformat()
        except ValueError:
            pass
    return ""


def extrair_dados_cpf(texto: str, cpf: str) -> Dict[str, Any]:
    esperado = re.sub(r"\D", "", cpf)
    # O BigData inclui CPFs/nascimentos de parentes depois dos dados do titular.
    texto = re.split(
        r"(?im)^\s*[-=]*\s*(?:PARENTES|POSS[ÍI]VEIS\s+VIZINHOS|VIZINHOS|RELACIONAMENTOS)\b[^\n]*",
        texto, maxsplit=1,
    )[0]
    encontrados = {
        re.sub(r"\D", "", m)
        for m in re.findall(r"\bCPF\s*:\s*(\d{3}\.?\d{3}\.?\d{3}-?\d{2})\b", texto, re.I)
    }
    if encontrados != {esperado}:
        raise ValueError("Mind7 devolveu dados sem confirmação do CPF consultado")
    dados = {"cpf": esperado, "birth_date": "", "cep": "", "house_number": ""}
    nasc = re.search(r"\b(?:data\s+de\s+)?nasc(?:imento|\.)?\s*:?\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})", texto, re.I)
    if nasc:
        dados["birth_date"] = normalizar_nascimento(nasc.group(1))
    linhas = [l.strip() for l in texto.splitlines() if re.search(r"\b\d{5}-?\d{3}\b", l)]
    endereco = next((l for l in linhas if "receita federal" in l.lower()), linhas[0] if linhas else "")
    if endereco:
        dados["cep"] = re.search(r"\b(\d{5}-?\d{3})\b", endereco).group(1).replace("-", "")
        dados["house_number"] = extrair_numero_endereco(endereco)
    return dados

def _garantir_login_mind7(tab) -> bool:
    try:
        if urlparse(tab.url).hostname != urlparse(MIND7_LOGIN_URL).hostname:
            return False
        url_lower = tab.url.lower()
        if "acesso" in url_lower or "login" in url_lower:
            email_elem = tab.ele("@name=email", timeout=0.5) or tab.ele("@type=email", timeout=0.5) or tab.ele("#email", timeout=0.5)
            pass_elem = tab.ele("@name=senha", timeout=0.5) or tab.ele("@name=password", timeout=0.5) or tab.ele("@type=password", timeout=0.5) or tab.ele("#senha", timeout=0.5)

            if email_elem and not email_elem.value:
                try: email_elem.input(MIND7_USER)
                except Exception: pass
            if pass_elem and not pass_elem.value:
                try: pass_elem.input(MIND7_PASS)
                except Exception: pass

            log.info("[MIND7] Aguardando o login do usuário na tela inicial...")
            return False

        return True
    except Exception as exc:
        log.warning(f"[MIND7] Aviso ao checar tela de login: {exc}")
        return False

def _pegar_aba(page, keyword: str, fallback_url: str):
    try:
        tab = page.get_tab()
        if tab:
            if keyword in tab.url.lower():
                return tab
            if tab.url in ("about:blank", "chrome://newtab", "edge://newtab", ""):
                tab.get(fallback_url)
                return tab
    except Exception:
        pass

    for tid in page.get_tabs():
        tab = tid if hasattr(tid, "url") else page.get_tab(tid)
        if keyword in tab.url.lower():
            return tab
    return page.new_tab(fallback_url)


def _limpar_resultado_mind7(tab):
    tab.run_js('''
        const resultado = document.getElementById("conteudo-retorno");
        if (resultado) resultado.replaceChildren();
        const copiar = document.getElementById("copiar_dados");
        if (copiar) copiar.value = "";
    ''')


def _aguardar_resultado_mind7(tab, seconds: float) -> Optional[str]:
    deadline = time.monotonic() + seconds
    anterior = ""
    estavel_desde = time.monotonic()
    while time.monotonic() < deadline:
        botao = tab.ele("#botao-consultar", timeout=0)
        copiar = tab.ele("#copiar_dados", timeout=0)
        texto = (copiar.value or "").strip() if copiar else ""
        resultado = tab.ele("#conteudo-retorno", timeout=0)
        visivel = (resultado.raw_text or "").lower() if resultado and resultado.states.is_displayed else ""
        sem_dados = any(msg in visivel for msg in (
            "não foi possível encontrar", "nao foi possivel encontrar", "nenhum registro encontrado",
        ))
        # O portal reativa Consultar somente depois de inserir a resposta AJAX.
        dados = texto if re.search(r"\b(?:cpf|cnpj)\s*:", texto, re.I) else ""
        pronto = botao and botao.states.is_enabled and (dados or sem_dados)
        atual = dados if dados else visivel if sem_dados else ""
        if not pronto or atual != anterior:
            anterior = atual if pronto else ""
            estavel_desde = time.monotonic()
        elif time.monotonic() - estavel_desde >= 1.0:
            return dados or None
        time.sleep(0.25)
    raise TimeoutError(f"Mind7 não concluiu a consulta em {seconds:g}s")


def extrair_numero_endereco(bloco_str: str) -> str:
    """Extrai o número do imóvel a partir do texto do endereço, evitando estritamente CEPs, anos, quadras e lotes."""
    if not bloco_str:
        return ""

    # 1. Remover CEP e padrões de CEP do texto para não poluir a busca de número
    texto_sem_cep = re.sub(r"cep\s*:?\s*\d{5}-?\d{3}", "", bloco_str, flags=re.I)
    texto_sem_cep = re.sub(r"\b\d{5}-?\d{3}\b", "", texto_sem_cep)
    texto_sem_cep = re.sub(r"\b\d{8}\b", "", texto_sem_cep)

    # 2. Procurar padrões explícitos como ", 123", "Nº 123", "NUM 123", "N 123", "NR 123"
    m_exp = re.search(r"(?:,\s*|n[oº]?\s*[:.]?\s*|num\s*[:.]?\s*|nr\s*[:.]?\s*|numero\s*[:.]?\s*)(\d{1,5})\b", texto_sem_cep, re.I)
    if m_exp:
        num = m_exp.group(1)
        if not (1900 <= int(num) <= 2030): # evitar anos
            return num

    # 3. Procurar padrão Logradouro + número (ex: "Rua 10, 540" ou "Av Brasil 540")
    m_log = re.search(r"(?:rua|av|avenida|travessa|alameda|praca|praça|rodovia|estrada|reserva|condominio)\s+[^,]+,\s*(\d{1,5})\b", texto_sem_cep, re.I)
    if m_log:
        num = m_log.group(1)
        if not (1900 <= int(num) <= 2030):
            return num

    # 4. Procurar por Quadra/Lote (ex: Qd 10 Lt 20)
    m_qd = re.search(r"\b(qd|quadra)\s*[\w.-]+\s*(lt|lote)\s*[\w.-]+", texto_sem_cep, re.I)
    if m_qd:
        return m_qd.group(0).upper()

    return "S/N"

def _extrair_candidatos_mind7(texto: str) -> List[Dict[str, Any]]:
    """
    Algoritmo de Extração de Candidatos do MODELO BASE:
    Suporta formato em blocos (CPF:... NOME:...) e formato tabular separado por pipe (|).
    """
    linhas = texto.split("\n")
    cpfs_ja_vistos = set()
    candidatos = []

    blocos = []
    atual = []
    for line in linhas:
        if (line.strip().startswith("CPF:") or re.search(r"^\s*CPF:\s*\d", line)) and atual:
            blocos.append("\n".join(atual))
            atual = [line]
        else:
            atual.append(line)
    if atual:
        blocos.append("\n".join(atual))

    for bloco in blocos:
        cpf_match = re.search(r"CPF:\s*(\d{11})", bloco)
        if not cpf_match:
            cpf_match = re.search(r"CPF:\s*(\d{3}\.\d{3}\.\d{3}-\d{2})", bloco)
        if not cpf_match:
            continue

        cpf_limpo = re.sub(r"\D", "", cpf_match.group(1))
        if len(cpf_limpo) != 11 or len(set(cpf_limpo)) == 1:
            continue

        if "|" in bloco:
            partes = [p.strip() for p in bloco.split("|")]
            cpf_idx = -1
            for idx, p in enumerate(partes):
                if "CPF:" in p:
                    cpf_idx = idx
                    break
            if cpf_idx != -1 and cpf_idx + 1 < len(partes):
                nome_raw = partes[cpf_idx + 1]
            else:
                nome_raw = "Desconhecido"
        else:
            nome_match = re.search(r"NOME:\s*(.*)", bloco, re.I)
            nome_raw = nome_match.group(1).strip() if nome_match else "Desconhecido"

        cep = ""
        cep_match = re.search(r"\b(\d{5}-?\d{3})\b", bloco)
        if not cep_match:
            cep_match = re.search(r"\b(\d{8})\b", bloco)
            if cep_match:
                cep_cand = cep_match.group(1)
                if not (1000000 <= int(cep_cand) <= 99999999):
                    cep_match = None
        if cep_match:
            cep = cep_match.group(1).replace("-", "")

        age = ""
        nasc_match = re.search(r"nasc(?:imento)?.*?(\d{2}/\d{2}/(\d{4}))", bloco, re.I)
        if nasc_match:
            try: age = f"{datetime.now().year - int(nasc_match.group(2))} anos"
            except: pass
        else:
            date_gen = re.search(r"\b\d{2}/\d{2}/(\d{4})\b", bloco)
            if date_gen:
                try: age = f"{datetime.now().year - int(date_gen.group(1))} anos"
                except: pass

        house_number = extrair_numero_endereco(bloco)

        if cpf_limpo not in cpfs_ja_vistos:
            candidatos.append({
                "cpf": cpf_limpo,
                "nome": nome_raw,
                "cep": cep,
                "house_number": house_number,
                "age": age
            })
            cpfs_ja_vistos.add(cpf_limpo)

    # Fallback tabular do MODELO BASE
    if not candidatos:
        for linha_txt in linhas:
            if "CPF:" in linha_txt and "|" in linha_txt:
                partes = linha_txt.split("|")
                cpf_raw = ""
                nome_raw = ""
                for idx, p in enumerate(partes):
                    if "CPF:" in p:
                        cpf_raw = p.replace("CPF:", "").strip()
                        if idx + 1 < len(partes):
                            nome_raw = partes[idx + 1].strip()

                cpf_limpo = re.sub(r"\D", "", cpf_raw)
                if len(cpf_limpo) == 11 and len(set(cpf_limpo)) > 1 and nome_raw:
                    cep = ""
                    cep_match = re.search(r"\b(\d{5}-?\d{3})\b", linha_txt)
                    if cep_match:
                        cep = cep_match.group(1).replace("-", "")

                    age = ""
                    date_gen = re.search(r"\b\d{2}/\d{2}/(\d{4})\b", linha_txt)
                    if date_gen:
                        try: age = f"{datetime.now().year - int(date_gen.group(1))} anos"
                        except: pass

                    house_number = ""
                    digitos = re.findall(r"\b(\d{1,5})\b", linha_txt)
                    for d in digitos:
                        if 1900 <= int(d) <= 2030 or len(d) == 5:
                            continue
                        house_number = d
                        break

                    if cpf_limpo not in cpfs_ja_vistos:
                        candidatos.append({
                            "cpf": cpf_limpo,
                            "nome": nome_raw,
                            "cep": cep,
                            "house_number": house_number,
                            "age": age
                        })
                        cpfs_ja_vistos.add(cpf_limpo)

    return candidatos

def consultar_mind7_celular(page, phone: str, clean_name: str, tab=None) -> Optional[Dict[str, Any]]:
    tab = tab or _pegar_aba(page, "mind", MIND7_LOGIN_URL)
    url_lower = tab.url.lower()

    if "acesso" in url_lower or "login" in url_lower:
        _garantir_login_mind7(tab)
        raise Mind7Unavailable("Faça o login no Mind7 e retome a etapa")

    return _consultar_mind7_celular_browser_com_recovery(page, phone, clean_name, tab=tab)

def _consultar_mind7_celular_browser_com_recovery(page, phone: str, clean_name: str, tentativa: int = 1, tab=None) -> Optional[Dict[str, Any]]:
    tab = tab or _pegar_aba(page, "mind", MIND7_URL)

    try:
        if "consultas/celular" not in tab.url:
            tab.get(MIND7_URL)
            time.sleep(0.5)

        if not check_and_solve_cloudflare(tab) or not _garantir_login_mind7(tab):
            raise Mind7Unavailable("Mind7 aguarda login ou validação no navegador")

        campo = tab.ele("#documento", timeout=4)
        if not campo:
            raise Mind7Unavailable("Formulário de telefone do Mind7 indisponível")

        _limpar_resultado_mind7(tab)

        campo.input(phone, clear=True)
        time.sleep(0.3)

        botao = tab.ele("#botao-consultar", timeout=2)
        if not botao:
            raise Mind7Unavailable("Botão de consulta do Mind7 indisponível")
        botao.click()
        log.info(f"[MIND7] Clicou no botão de consultar para {phone}. Aguardando resultado...")

        tab.scroll.to_bottom()

        texto_copiar = _aguardar_resultado_mind7(tab, 30)
        if texto_copiar is None:
            return None

        candidatos = _extrair_candidatos_mind7(texto_copiar)
        if not candidatos:
            return None

        log.info(f"[MIND7] Telefone {phone} ('{clean_name}') ➔ {len(candidatos)} candidato(s) retornado(s):")
        matches = []
        for idx, cand in enumerate(candidatos, 1):
            score = calcular_score_nome(clean_name, cand["nome"])
            log.info(f"   ├─ [{idx}] {cand['nome']:35s} | CPF: {cand['cpf']} | Score: {score:.2f}")
            if score >= 0.80:
                matches.append({
                    "cpf": cand["cpf"],
                    "nome": cand["nome"],
                    "cep": cand.get("cep", ""),
                    "house_number": cand.get("house_number", ""),
                    "age": cand.get("age", ""),
                    "score": score
                })

        if matches:
            matches.sort(key=lambda x: x["score"], reverse=True)
            best = matches[0]
            log.info(f"   └─ ✅ MATCH ENCONTRADO: '{clean_name}' ~ '{best['nome']}' | CPF: {best['cpf']} (Score: {best['score']:.2f})")
            return best

        log.info(f"   └─ ❌ DIVERGÊNCIA: Nenhum dos {len(candidatos)} candidatos bateu com '{clean_name}' (threshold >= 0.80).")
        return None

    except Exception as exc:
        log.error(f"Erro na consulta Mind7 Browser ({phone}): {exc}")
        raise

def consultar_mind7_cpf(page, cpf: str, tab=None) -> Optional[Dict[str, Any]]:
    """
    Enriquecimento por CPF no Mind7 utilizando o Algoritmo do MODELO BASE:
    Prioriza a linha da RECEITA FEDERAL para extrair CEP e Número.
    """
    tab = tab or _pegar_aba(page, "mind", MIND7_CPF_URL)

    try:
        if "consultas/cpf" not in tab.url:
            tab.get(MIND7_CPF_URL)
            time.sleep(0.5)
        if not check_and_solve_cloudflare(tab) or not _garantir_login_mind7(tab):
            raise Mind7Unavailable("Mind7 aguarda login ou validação no navegador")

        campo = tab.ele("#documento", timeout=4)
        if not campo:
            # O portal às vezes abre o painel inicial no lugar da consulta por CPF.
            tab.get(MIND7_CPF_URL)
            if not check_and_solve_cloudflare(tab) or not _garantir_login_mind7(tab):
                raise Mind7Unavailable("Mind7 aguarda login ou validação no navegador")
            campo = tab.ele("#documento", timeout=4)
            if not campo:
                raise Mind7Unavailable("Formulário de CPF do Mind7 indisponível")

        _limpar_resultado_mind7(tab)

        campo.input(cpf, clear=True)
        time.sleep(0.2)

        botao = tab.ele("#botao-consultar", timeout=2)
        if not botao:
            raise Mind7Unavailable("Botão de consulta do Mind7 indisponível")
        botao.click()

        texto_copiar = _aguardar_resultado_mind7(tab, 25)
        if texto_copiar is None:
            return None

        return extrair_dados_cpf(texto_copiar, cpf)
    except Exception as exc:
        log.error(f"Erro no enriquecimento CPF Mind7 ({cpf}): {exc}")
        raise
