"""
Módulo responsável pelas operações de Web Scraping usando DrissionPage (Conectando a navegador existente).
"""

import os
import sys
import time
import re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.automation.mind7_rate_limit import Mind7AccessBlocked, ensure_mind7_allowed
from src.automation.mind7_scraper import _verificar_bloqueio_mind7, _limpar_resultado_mind7, Mind7Unavailable, enviar_consulta_mind7
from datetime import date, datetime
from DrissionPage import ChromiumPage, ChromiumOptions
from typing import Dict, Any, Optional
from app.config import settings


AUDIT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "logs",
    "credline_audit",
)
os.makedirs(AUDIT_DIR, exist_ok=True)


def _data_nascimento_explicita(texto: str) -> str:
    """Aceita somente data completa rotulada como nascimento, jamais ano ou idade."""
    match = re.search(
        r"\b(?:data\s+de\s+)?nasc(?:imento|\.)?\s*:\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})",
        texto, re.I,
    )
    if not match:
        return ""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            nascimento = datetime.strptime(match.group(1), fmt).date()
            hoje = date.today()
            if date(hoje.year - 120, 1, 1) <= nascimento <= hoje:
                return nascimento.isoformat()
        except ValueError:
            continue
    return ""


def _audit_enabled() -> bool:
    return os.getenv("CREDLINE_AUDIT", "1") == "1"


def _stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _save_audit(tab, cpf: str, stage: str, note: str = ""):
    """
    Salva evidencias do Credline por CPF/etapa para auditoria operacional:
      - trecho do body.text
      - valor atual do campo CPF lido do DOM
      - tentativa de screenshot
    """
    if not _audit_enabled():
        return

    base_name = f"{_stamp()}_{cpf}_{stage}"
    txt_path = os.path.join(AUDIT_DIR, f"{base_name}.txt")
    png_path = os.path.join(AUDIT_DIR, f"{base_name}.png")

    body_text = ""
    input_value = ""
    input_id = ""
    error = ""

    try:
        body = tab.ele("tag:body", timeout=1)
        body_text = (body.text or "")[:3000] if body else ""
    except Exception as exc:
        error += f"body_text_error={exc}\n"

    try:
        data = tab.run_js(
            """
            const sel = ['#ids-input-0', '#ids-input-4', '#mat-input-0'];
            for (const s of sel) {
              const el = document.querySelector(s);
              if (el) return { id: el.id || '', value: el.value || '' };
            }
            const anyInput = document.querySelector('input');
            if (anyInput) return { id: anyInput.id || '', value: anyInput.value || '' };
            return { id: '', value: '' };
            """
        ) or {}
        input_id = str(data.get("id", ""))
        input_value = str(data.get("value", ""))
    except Exception as exc:
        error += f"input_read_error={exc}\n"

    try:
        tab.get_screenshot(path=png_path)
    except Exception as exc:
        error += f"screenshot_error={exc}\n"

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"cpf={cpf}\n")
        f.write(f"stage={stage}\n")
        f.write(f"note={note}\n")
        f.write(f"url={getattr(tab, 'url', '')}\n")
        f.write(f"input_id={input_id}\n")
        f.write(f"input_value={input_value}\n")
        if error:
            f.write(f"errors=\n{error}\n")
        f.write("\nbody_text_preview=\n")
        f.write(body_text)

def _porta_padrao_itau() -> int:
    return int(os.getenv("ITAU_DEBUG_PORT", "9224"))


def iniciar_navegador(porta: Optional[int] = None) -> ChromiumPage:
    """
    Conecta a uma instância já aberta do Chrome/Edge rodando na porta de debug configurada.
    """
    porta = int(porta or _porta_padrao_itau())
    browser_path = os.getenv("EDGE_EXE", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    co = ChromiumOptions()
    co.set_browser_path(browser_path)
    co.set_local_port(porta)
    # Não definimos headless porque estamos conectando a um navegador visual real
    
    try:
        page = ChromiumPage(co)
        return page
    except Exception as e:
        print(f"Erro ao conectar no navegador. Ele foi iniciado com --remote-debugging-port={porta}? Erro: {e}")
        raise

def obter_aba_existente(page: ChromiumPage, keyword: str, fallback_url: str):
    """
    Procura uma aba já aberta que contenha a keyword na URL.
    Se não achar, cria uma nova.
    """
    for tab_id in page.get_tabs():
        aba = page.get_tab(tab_id)
        if keyword.lower() in aba.url.lower():
            return aba
    print(f"Aba do {keyword} não encontrada aberta. Criando uma nova...")
    return page.new_tab(fallback_url)


def consultar_mind7(page: ChromiumPage, telefone: str, pushName: str) -> Optional[Dict[str, str]]:
    """
    Usa a aba já aberta do Mind7 para buscar os dados.
    """
    ensure_mind7_allowed()
    url_busca = "https://mind-7.org/painel/consultas/celular/"
    tab = obter_aba_existente(page, "mind", url_busca)
    _verificar_bloqueio_mind7(tab)
    
    try:
        print(f"Acessando Mind7 na aba: {tab.url}")
        
        # Se a página mudou muito ou não está na tela certa, forçamos o load
        if "consultas/celular" not in tab.url:
            tab.get(url_busca)
            tab.wait.load_start()
        
        print(f"Buscando telefone: {telefone}")
        _verificar_bloqueio_mind7(tab)
        busca_input = tab.ele('#documento', timeout=10)
        if not busca_input:
            raise Mind7Unavailable("Formulário de telefone Mind7 indisponível")
        _limpar_resultado_mind7(tab)
        
        # Um único envio evita duas consultas para o mesmo telefone.
        busca_input.input(telefone, clear=True)
        botao = tab.ele('#botao-consultar', timeout=5)
        if not botao:
            _verificar_bloqueio_mind7(tab)
            raise Mind7Unavailable("Botão de consulta Mind7 indisponível")
        texto_copiar = enviar_consulta_mind7(tab, botao, timeout=25)
        if texto_copiar is None:
            return None

        import re
        from app.utils.formatters import limpar_nome, comparar_primeiro_nome

        linhas_texto = texto_copiar.split("\n")
        cpfs_ja_vistos = set()
        candidatos = []
        
        # Divide o texto_copiar em blocos de candidatos
        blocos_txt = []
        bloco_atual = []
        for linha in linhas_texto:
            if (linha.strip().startswith("CPF:") or re.search(r"^\s*CPF:\s*\d", linha)) and bloco_atual:
                blocos_txt.append("\n".join(bloco_atual))
                bloco_atual = [linha]
            else:
                bloco_atual.append(linha)
        if bloco_atual:
            blocos_txt.append("\n".join(bloco_atual))

        def extrair_numero_endereco(bloco_str: str) -> str:
            lines = bloco_str.split("\n")
            for line in lines:
                if any(k in line.lower() for k in ["endereço", "endereco", "logradouro", "rua", "av", "avenida", "travessa"]):
                    m = re.search(r"(?:rua|av|avenida|travessa|alameda|praca|rodovia|logradouro|endereco).*?,\s*(\d+)\b", line, re.I)
                    if m:
                        return m.group(1)
                    m = re.search(r"(?:rua|av|avenida|travessa|alameda|praca|rodovia|logradouro|endereco).*?\s+(\d+)\b", line, re.I)
                    if m:
                        return m.group(1)
            for line in lines:
                if any(k in line.lower() for k in ["endereço", "endereco", "logradouro", "rua", "av", "avenida", "travessa", "bairro"]):
                    digitos = re.findall(r"\b(\d{1,5})\b", line)
                    for d in digitos:
                        if 1900 <= int(d) <= 2030 or len(d) == 5:
                            continue
                        return d
            return ""

        for bloco in blocos_txt:
            cpf_match = re.search(r"CPF:\s*(\d{11})", bloco)
            if not cpf_match:
                cpf_match = re.search(r"CPF:\s*(\d{3}\.\d{3}\.\d{3}-\d{2})", bloco)
            if not cpf_match:
                continue
                
            cpf_limpo = re.sub(r"\D", "", cpf_match.group(1))
            if len(cpf_limpo) != 11 or len(set(cpf_limpo)) == 1:
                continue
                
            nome_match = re.search(r"NOME:\s*(.*)", bloco, re.I)
            nome_raw = nome_match.group(1).strip() if nome_match else "Desconhecido"
            
            cep = ""
            cep_match = re.search(r"\b(\d{5}-\d{3})\b", bloco)
            if not cep_match:
                cep_match = re.search(r"\b(\d{8})\b", bloco)
                if cep_match:
                    cep_candidato = cep_match.group(1)
                    if not (1000000 <= int(cep_candidato) <= 99999999):
                        cep_match = None
            if cep_match:
                cep = cep_match.group(1).replace("-", "")
                
            birth_date = _data_nascimento_explicita(bloco)
                    
            numero = extrair_numero_endereco(bloco)
            
            if cpf_limpo not in cpfs_ja_vistos:
                candidatos.append({
                    "nome_completo": nome_raw,
                    "cpf": cpf_limpo,
                    "cep": cep,
                    "numero_casa": numero,
                    "birth_date": birth_date
                })
                cpfs_ja_vistos.add(cpf_limpo)

        # Fallback tabular
        if not candidatos:
            for linha_txt in linhas_texto:
                if "CPF:" in linha_txt and "|" in linha_txt:
                    partes = linha_txt.split("|")
                    cpf_raw = ""
                    nome_raw = ""
                    for idx, p in enumerate(partes):
                        if "CPF:" in p:
                            cpf_raw = p.replace("CPF:", "").strip()
                            if idx + 1 < len(partes):
                                nome_raw = partes[idx+1].strip()
                    
                    cpf_limpo = re.sub(r"\D", "", cpf_raw)
                    if len(cpf_limpo) == 11 and len(set(cpf_limpo)) > 1 and nome_raw:
                        cep = ""
                        cep_match = re.search(r"\b(\d{5}-?\d{3})\b", linha_txt)
                        if cep_match:
                            cep = cep_match.group(1).replace("-", "")
                        
                        birth_date = _data_nascimento_explicita(linha_txt)
                            
                        numero = ""
                        digitos = re.findall(r"\b(\d{1,5})\b", linha_txt)
                        for d in digitos:
                            if 1900 <= int(d) <= 2030 or len(d) == 5:
                                continue
                            numero = d
                            break
                            
                        if cpf_limpo not in cpfs_ja_vistos:
                            candidatos.append({
                                "nome_completo": nome_raw,
                                "cpf": cpf_limpo,
                                "cep": cep,
                                "numero_casa": numero,
                                "birth_date": birth_date
                            })
                            cpfs_ja_vistos.add(cpf_limpo)

        # Selecionar o candidato correto comparando o primeiro nome
        for cand in candidatos:
            if comparar_primeiro_nome(pushName, cand["nome_completo"]):
                return cand

        if candidatos:
            return candidatos[0]

        return None

    except (Mind7AccessBlocked, Mind7Unavailable, TimeoutError):
        raise
    except Exception as e:
        print(f"Erro na consulta Mind7: {e}")
        raise Mind7Unavailable("Falha na consulta Mind7; confira antes de reenviar.") from e

def _marcar_veiculo_usado(tab) -> bool:
    """
    Garante a marcação do checker/radio/checkbox/toggle de 'Veículo Usado' / 'Usado' no Credline.
    Suporta múltiplos formatos: Radio button, Checkbox, Switch/Toggle e componentes Angular IDS/Material.
    """
    try:
        marcado = tab.run_js("""
            const selectors = [
                'input[type="radio"]', 
                'input[type="checkbox"]', 
                'ids-radio', 
                'ids-checkbox', 
                'ids-toggle', 
                'ids-switch', 
                'mat-radio-button', 
                'mat-checkbox',
                'mat-slide-toggle'
            ];
            
            const elements = Array.from(document.querySelectorAll(selectors.join(',')));
            for (let el of elements) {
                const val = (el.value || '').toLowerCase();
                const id = (el.id || '').toLowerCase();
                const name = (el.name || '').toLowerCase();
                const fcn = (el.getAttribute('formcontrolname') || '').toLowerCase();
                const datacy = (el.getAttribute('data-cy') || '').toLowerCase();
                const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                const labelText = (el.closest('label')?.textContent || el.parentElement?.textContent || el.textContent || '').toLowerCase();

                const isUsado = [val, id, name, fcn, datacy, aria, labelText].some(txt => 
                    txt.includes('usad') || txt.includes('seminov') || txt === 'u' || txt.includes('used')
                );

                if (isUsado) {
                    const isChecked = el.checked || 
                                      el.getAttribute('checked') === 'true' || 
                                      el.classList.contains('ids-radio--checked') || 
                                      el.classList.contains('mat-radio-checked') ||
                                      el.classList.contains('mat-checkbox-checked') ||
                                      el.getAttribute('aria-checked') === 'true';
                    if (!isChecked) {
                        const target = el.querySelector('input') || el.querySelector('label') || el;
                        target.click();
                        target.dispatchEvent(new Event('change', { bubbles: true }));
                        target.dispatchEvent(new Event('input', { bubbles: true }));
                        return true;
                    }
                    return true;
                }
            }

            const textNodes = Array.from(document.querySelectorAll('label, span, p, div, button, a'));
            for (let el of textNodes) {
                const t = (el.textContent || '').trim().toLowerCase();
                if (t === 'usado' || t === 'veículo usado' || t === 'veiculo usado' || t === 'usados' || t === 'seminovo' || t.startsWith('usado ') || t.endsWith(' usado')) {
                    const parent = el.closest('label') || el.closest('.ids-radio') || el.closest('.mat-radio-button') || el.closest('.ids-checkbox') || el;
                    const inp = parent.querySelector('input');
                    if (inp && (inp.checked || inp.getAttribute('aria-checked') === 'true')) {
                        return true;
                    }
                    (inp || parent).click();
                    return true;
                }
            }

            return false;
        """)
        if marcado:
            print("    [CREDLINE] Opção/Checker 'Veículo Usado' marcada com sucesso.")
            return True

        seletores_dp = [
            'xpath://input[(@type="radio" or @type="checkbox") and (contains(@value, "USADO") or contains(@value, "usado") or contains(@value, "USED") or contains(@formcontrolname, "usad") or contains(@data-cy, "usad") or contains(@data-cy, "used"))]',
            'xpath://label[contains(translate(., "USADO", "usado"), "usado") or contains(translate(., "SEMINOVO", "seminovo"), "seminovo")]',
            'xpath://span[contains(translate(., "USADO", "usado"), "usado") or contains(translate(., "SEMINOVO", "seminovo"), "seminovo")]',
            'xpath://div[contains(@class, "radio") or contains(@class, "checkbox") or contains(@class, "ids") or contains(@class, "mat")][contains(translate(., "USADO", "usado"), "usado")]',
            '@data-cy=vehicle-condition-used',
            '@data-cy=used-vehicle',
            '@data-cy=used',
            'text=Veículo Usado',
            'text=Veiculo Usado',
            'text=Usado'
        ]
        for sel in seletores_dp:
            try:
                el = tab.ele(sel, timeout=0.2)
                if el and el.states.is_displayed:
                    el.click()
                    print(f"    [CREDLINE] Opção 'Veículo Usado' marcada via seletor: {sel}")
                    return True
            except Exception:
                continue

    except Exception:
        pass
    return False

def consultar_itau_credline(page: ChromiumPage, cpf: str):
    """
    Usa a aba já aberta do simulador do Itaú, joga o CPF e verifica status.
    """
    url_simulador = "https://credlineitau.com.br/"
    tab = obter_aba_existente(page, "credline", url_simulador)
    
    try:
        print(f"Acessando Itaú Credline na aba: {tab.url}")
        
        if "credlineitau.com.br" not in tab.url:
            tab.get(url_simulador)
            tab.wait.load_start()
        
        # Inserir CPF no simulador
        print(f"Simulando CPF: {cpf}")
        
        # Tenta achar o botão "limpar" caso já tenha um CPF preenchido
        try:
            limpar = tab.ele('text:Limpar', timeout=2)
            if limpar: limpar.click()
        except:
            pass
 
        _save_audit(tab, cpf, "antes_input", "inicio da etapa no credline")
        tab.ele('#ids-input-0', timeout=10).input(cpf, clear=True)
        _save_audit(tab, cpf, "apos_input", "cpf enviado pelo bot")
        tab.ele('#continuar-button').click()
        _save_audit(tab, cpf, "apos_continuar", "botao continuar clicado")
        
        # Aguardar o processamento da simulação
        time.sleep(5) # Delay estático para esperar a página mudar
        
        # REGRA DE OURO: Analisar o DOM da página final de resultado
        dom_text = tab.html.lower()
        _save_audit(tab, cpf, "resultado", "dom apos consulta")
        
        import re
        is_nao_correntista = "não correntista" in dom_text
        is_correntista = "correntista" in dom_text
        
        if is_nao_correntista or is_correntista:
            status_tipo = "NAO CORRENTISTA" if is_nao_correntista else "CORRENTISTA"
            print(f"Status: {status_tipo} APROVADO. Iniciando simulação por placa...")
            
            # Tenta carregar dados do simulacao_dados.json
            placa = ""
            valor = ""
            try:
                import json
                json_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "simulacao_dados.json")
                if os.path.exists(json_path):
                    with open(json_path, "r", encoding="utf-8") as f:
                        dados_sim = json.load(f)
                        placa = dados_sim.get("placa", "")
                        valor = dados_sim.get("valor", "")
            except Exception:
                pass
                
            if placa and valor:
                try:
                    # Fechar modal informativo de pré-aprovado se aparecer
                    try:
                        modal_fechar = tab.ele('xpath://button[contains(., "fechar")]', timeout=2)
                        if modal_fechar: 
                            modal_fechar.click()
                            print("Modal informativo fechado.")
                    except Exception:
                        pass
                        
                    # Roda simulação por placa (lógica idêntica do DrissionPage)
                    tab.scroll.to_bottom()
                    time.sleep(0.5)
                    _marcar_veiculo_usado(tab)
                    
                    btn_placa = tab.ele('xpath://button[@role="tab" and contains(., "Placa")]', timeout=5)
                    if not btn_placa: btn_placa = tab.ele('text=Placa', timeout=3)
                    if btn_placa: btn_placa.click()
                    
                    time.sleep(1.0)
                    _marcar_veiculo_usado(tab)
                    
                    input_placa = tab.ele('@data-cy=vehicle-identification', timeout=5)
                    if not input_placa:
                        input_placa = tab.ele('xpath://input[@data-cy="vehicle-identification"]', timeout=3)
                    if not input_placa:
                        input_placa = tab.ele('input[formcontrolname="vehicleIdentification"]', timeout=3)
                    if input_placa:
                        input_placa.input(placa, clear=True)
                    
                    time.sleep(0.5)
                    
                    btn_buscar = tab.ele('xpath://button[contains(., "Buscar veículo")]', timeout=5)
                    if btn_buscar: btn_buscar.click()
                    
                    time.sleep(1.5)
                    
                    radio_veiculo = tab.ele('xpath://input[@type="radio" and @formcontrolname="vehicle"]', timeout=10)
                    if not radio_veiculo:
                        radio_veiculo = tab.ele('input[type="radio"][formcontrolname="vehicle"]', timeout=5)
                    if radio_veiculo:
                        radio_veiculo.click()
                    
                    time.sleep(0.5)
                    _marcar_veiculo_usado(tab)
                    
                    valor_sanitizado = re.sub(r"[^0-9.,]", "", valor)
                    tab.run_js("""
                        const el = document.querySelector('input[data-cy="vehicle-value"]') || document.querySelector('input[formcontrolname="vehicleValue"]');
                        if (el) {
                            el.value = arguments[0];
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    """, valor_sanitizado)
                    
                    time.sleep(0.5)
                    _marcar_veiculo_usado(tab)
                    
                    btn_simular = tab.ele('@data-cy=create-simulation-button', timeout=5)
                    if not btn_simular:
                        btn_simular = tab.ele('xpath://button[@data-cy="create-simulation-button"]', timeout=3)
                    if btn_simular:
                        btn_simular.click()
                    
                    # Tentamos primeiro o XPath super-específico associado ao label "Valor à financiar"
                    xpath_financiado = 'xpath://p[contains(text(), "Valor à financiar") or contains(text(), "Valor a financiar")]/parent::div/following-sibling::div/p'
                    val_el = tab.ele(xpath_financiado, timeout=15)
                    
                    # Caso o XPath não encontre, tentamos obter múltiplos data-cy e pegar o segundo (que é o valor a financiar)
                    if not val_el:
                        val_els = tab.eles('@data-cy=vehicle-value-description')
                        if not val_els:
                            val_els = tab.eles('xpath://p[@data-cy="vehicle-value-description"]')
                        if val_els:
                            val_el = val_els[1] if len(val_els) >= 2 else val_els[0]
                            
                    if val_el:
                        valor_financiado = val_el.text.strip()
                        print(f"💰 Simulação concluída! Valor Financiado: {valor_financiado}")
                        return {"status": status_tipo, "valor": valor_financiado}
                except Exception as e:
                    print(f"Erro na simulação por placa: {e}")
                    return {"status": status_tipo, "valor": "ERRO_SIMULACAO"}
            
            return {"status": status_tipo, "valor": None}
            
        if "reprovado" in dom_text or "não aprovado" in dom_text:
            print("Status: Reprovado no crédito.")
            return {"status": "REPROVADO", "valor": None}
            
        print("Atenção: Não foi possível determinar claramente o status no DOM.")
        return False
 
    except Exception as e:
        print(f"Erro na consulta Itaú Credline: {e}")
        return False
