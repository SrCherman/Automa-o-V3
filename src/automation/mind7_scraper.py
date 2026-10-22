"""
mind7_scraper.py - Automação Híbrida Adaptativa do Portal Mind7 (Métodos de Coleta e Matching do MODELO BASE)
"""

import re
import time
import logging
from datetime import date, datetime
from typing import Optional, Dict, Any, List
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

        # 1. LIMPEZA RÍGIDA DO DOM PARA EVITAR DADOS VELHOS
        tab.run_js('if(document.getElementById("copiar_dados")) document.getElementById("copiar_dados").value = "";')
        tab.run_js('document.querySelectorAll("table").forEach(t => t.remove());')
        tab.run_js('document.querySelectorAll(".list-group-item").forEach(t => t.remove());')
        tab.run_js("""
            document.querySelectorAll('.alert, .badge, .text-danger, span, p, div.alert-danger').forEach(el => {
                if (el.children.length === 0 || el.classList.contains('alert') || el.classList.contains('text-danger')) {
                    let txt = (el.textContent || '').toLowerCase();
                    if (txt.includes('não foi possível') || txt.includes('nao foi possivel') || txt.includes('nenhum registro') || txt.includes('não encontrado')) {
                        el.remove();
                    }
                }
            });
        """)

        campo.input(phone, clear=True)
        time.sleep(0.3)

        botao = tab.ele("#botao-consultar", timeout=2)
        if botao:
            botao.click()
            log.info(f"[MIND7] Clicou no botão de consultar para {phone}. Aguardando resultado...")
        else:
            campo.input("\n")
            log.info(f"[MIND7] Enviou Enter para consultar {phone}. Aguardando resultado...")

        tab.scroll.to_bottom()

        start_time = time.time()
        max_wait = 30.0
        texto_copiar = ""
        achou_resultado = False

        while time.time() - start_time < max_wait:
            elapsed = time.time() - start_time

            copiar_area = tab.ele("#copiar_dados", timeout=0)
            if copiar_area:
                texto = copiar_area.value or ""
                if "cpf:" in texto.lower() or "cnpj:" in texto.lower():
                    # Aguardar estabilização do textarea do Mind7 (MODELO BASE)
                    texto_anterior = ""
                    for _ in range(6):
                        time.sleep(0.5)
                        texto_atual = tab.ele("#copiar_dados", timeout=1).value or ""
                        if texto_atual == texto_anterior and texto_atual != "":
                            break
                        texto_anterior = texto_atual

                    texto_copiar = texto_anterior if texto_anterior else texto
                    achou_resultado = True
                    log.info(f"[MIND7] Resultado carregado e estabilizado para {phone} em {elapsed:.1f}s")
                    break

            html_lower = (tab.html or "").lower()
            if "não foi possível encontrar" in html_lower or "nao foi possivel encontrar" in html_lower or "nenhum registro encontrado" in html_lower:
                log.info(f"[MIND7 SEM REGISTRO] Telefone {phone} sem dados no Mind7.")
                return None

            time.sleep(0.5)

        if not achou_resultado:
            raise TimeoutError("Mind7 não concluiu a consulta por telefone em 30s")

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
            raise Mind7Unavailable("Formulário de CPF do Mind7 indisponível")

        tab.run_js('if(document.getElementById("copiar_dados")) document.getElementById("copiar_dados").value = "";')
        tab.run_js('document.querySelectorAll("table").forEach(t => t.remove());')
        tab.run_js('document.querySelectorAll(".list-group-item").forEach(t => t.remove());')
        tab.run_js("""
            document.querySelectorAll('.alert, .badge, .text-danger, span, p, div.alert-danger').forEach(el => {
                if (el.children.length === 0 || el.classList.contains('alert') || el.classList.contains('text-danger')) {
                    let txt = (el.textContent || '').toLowerCase();
                    if (txt.includes('não foi possível') || txt.includes('nao foi possivel') || txt.includes('nenhum registro') || txt.includes('não encontrado')) {
                        el.remove();
                    }
                }
            });
        """)

        campo.input(cpf, clear=True)
        time.sleep(0.2)

        botao = tab.ele("#botao-consultar", timeout=2)
        if botao:
            botao.click()
        else:
            campo.input("\n")

        start_time = time.time()
        texto_copiar = ""
        while time.time() - start_time < 25.0:
            copiar_area = tab.ele("#copiar_dados", timeout=0)
            if copiar_area:
                texto = copiar_area.value or ""
                if "cpf:" in texto.lower() or len(texto.strip()) > 30:
                    # Aguardar estabilização do textarea
                    texto_anterior = ""
                    for _ in range(4):
                        time.sleep(0.3)
                        texto_atual = tab.ele("#copiar_dados", timeout=1).value or ""
                        if texto_atual == texto_anterior and texto_atual != "":
                            break
                        texto_anterior = texto_atual
                    texto_copiar = texto_anterior if texto_anterior else texto
                    break

            html_lower = (tab.html or "").lower()
            if "não foi possível encontrar" in html_lower or "nao foi possivel encontrar" in html_lower or "nenhum registro encontrado" in html_lower:
                log.info(f"[MIND7 CPF] CPF {cpf} sem registros no Mind7.")
                return None

            time.sleep(0.3)

        if not texto_copiar:
            raise TimeoutError("Mind7 não concluiu a consulta por CPF em 25s")

        return extrair_dados_cpf(texto_copiar, cpf)
    except Exception as exc:
        log.error(f"Erro no enriquecimento CPF Mind7 ({cpf}): {exc}")
        raise
