"""
credline_scraper.py - Automação Headless do Portal Itaú Credline (Baseado no MODELO BASE de Alta Estabilidade)
"""

import re
import time
import unicodedata
import logging
from typing import Dict, Any, Optional
from src.config import CREDLINE_URL, CREDLINE_USER, CREDLINE_PASS

log = logging.getLogger("credline_scraper")
VALOR_ENTRADA = "30.000,00"

def normalizar_texto(texto: str) -> str:
    """Normaliza texto de tela para comparacoes sem depender de acentos."""
    texto = str(texto or "").lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.split())

def _decidir_credline(texto_normalizado: str):
    if "nao correntista" in texto_normalizado and "ja e correntista" not in texto_normalizado:
        return True, "NAO CORRENTISTA", "texto contem 'nao correntista'"

    correntistas_termos = [
        "cliente correntista",
        "ja e correntista",
        "ja possui conta",
    ]
    termo_correntista = next((t for t in correntistas_termos if t in texto_normalizado), None)
    if termo_correntista:
        return True, "CORRENTISTA", f"correntista detectado: {termo_correntista}"

    falhas_transitorias = [
        "indisponivel",
        "tente novamente",
        "erro ao consultar",
    ]
    termo = next((t for t in falhas_transitorias if t in texto_normalizado), None)
    if termo:
        return None, "PENDENTE ITAU", f"falha transitoria detectada: {termo}"

    reprovacoes = [
        "nao temos condicoes aprovaveis",
        "sem condicoes aprovaveis",
        "condicoes nao aprovaveis",
        "reprovado",
        "nao aprovado",
        "sem ofertas",
        "nao existem",
        "cpf invalido",
    ]
    termo = next((t for t in reprovacoes if t in texto_normalizado), None)
    if termo:
        return False, "REPROVADO", f"reprovacao detectada: {termo}"

    return None, "PENDENTE ITAU", "resultado nao reconhecido"

def _garantir_login_credline(tab) -> bool:
    """
    Detecta e realiza o login automático no Itaú Credline tanto por URL quanto por presença
    dos campos de login (e-mail/senha) no DOM do Angular ou iframe.
    """
    try:
        url_lower = (tab.url or "").lower()
        target = tab
        # Sondagem imediata: a ausência de iframe não deve atrasar cada CPF.
        iframes = tab.eles("tag:iframe", timeout=0)
        for iframe in iframes:
            try:
                frame = tab.get_frame(iframe)
                if frame.ele("#username", timeout=0.2) or frame.ele("#password", timeout=0.2) or frame.ele("input[type='password']", timeout=0.2):
                    target = frame
                    break
            except Exception:
                pass

        user_elem = (
            target.ele("#username", timeout=0.2) or 
            target.ele("@name=username", timeout=0.2) or 
            target.ele("input[type='email']", timeout=0.2) or
            target.ele("input[formcontrolname='email']", timeout=0.2) or
            target.ele("input[placeholder*='e-mail']", timeout=0.2) or
            target.ele("input[placeholder*='email']", timeout=0.2) or
            target.ele("#email", timeout=0.2)
        )
        pass_elem = (
            target.ele("#password", timeout=0.2) or 
            target.ele("@name=password", timeout=0.2) or 
            target.ele("input[type='password']", timeout=0.2) or
            target.ele("input[formcontrolname='password']", timeout=0.2) or
            target.ele("input[placeholder*='senha']", timeout=0.2) or
            target.ele("#senha", timeout=0.2)
        )

        if user_elem or pass_elem:
            log.info("[CREDLINE] Tela de login do Itaú Credline identificada. Preenchendo credenciais...")
            if user_elem and CREDLINE_USER:
                user_elem.input(CREDLINE_USER, clear=True)
                time.sleep(0.2)
            if pass_elem and CREDLINE_PASS:
                pass_elem.input(CREDLINE_PASS, clear=True)
                time.sleep(0.2)

            btn = (
                target.ele("#kc-login", timeout=0.3) or 
                target.ele("@name=login", timeout=0.3) or 
                target.ele("@type=submit", timeout=0.3) or 
                target.ele("xpath://button[contains(translate(., 'ENTRAR', 'entrar'), 'entrar')]", timeout=0.3) or
                target.ele("text=entrar", timeout=0.3) or
                target.ele("text=Entrar", timeout=0.3) or
                target.ele("button", timeout=0.3)
            )
            if btn:
                btn.click()
                log.info("[CREDLINE] Botão Entrar clicado. Aguardando autenticação...")
                time.sleep(3.5)
                return True
        return False
    except Exception as exc:
        log.warning(f"[CREDLINE] Erro ao verificar login no Itaú: {exc}")
        return False

def _pegar_aba(page, keyword: str, fallback_url: str):
    try:
        tabs = [page.get_tab(tid) for tid in page.tab_ids]
    except Exception:
        tabs = []

    # 1. Procurar aba com a keyword específica (ex: new-simulator)
    for tab in tabs:
        try:
            if keyword in (tab.url or "").lower():
                return tab
        except Exception:
            continue

    # 2. Procurar aba no mesmo domínio (ex: credline)
    for tab in tabs:
        try:
            url_l = (tab.url or "").lower()
            if "credline" in url_l and "login" not in url_l and "auth" not in url_l:
                tab.get(fallback_url)
                return tab
        except Exception:
            continue

    # 3. Reutilizar aba vazia ou primeira disponível
    for tab in tabs:
        try:
            url_l = (tab.url or "").lower()
            if url_l in ("about:blank", "chrome://newtab", "edge://newtab", ""):
                tab.get(fallback_url)
                return tab
        except Exception:
            continue

    if tabs:
        tabs[0].get(fallback_url)
        return tabs[0]

    return page.new_tab(fallback_url)

def _achar_campo_cpf(tab, timeout: float = 3.0):
    # Um único prazo para todos os seletores, sem depender dos IDs dinâmicos
    # ids-input-N ou confundir os campos de moeda/placa com o CPF.
    seletor = (
        'css:input[data-cy*="document"], input[data-cy*="cpf"], '
        'input[idsmask*="000.000.000-00"], input[formcontrolname="cpf"], '
        'input[formcontrolname="document"], input[aria-label*="CPF"], '
        'input[aria-label*="cpf"]'
    )
    limite = time.monotonic() + timeout
    while True:
        candidatos = list(tab.eles(seletor, timeout=0))
        for label in tab.eles("tag:label", timeout=0):
            if normalizar_texto(label.text) == "cpf":
                for_id = label.attr("for")
                if for_id:
                    candidatos.append(tab.ele(f"#{for_id}", timeout=0))
                else:
                    parent = label.parent()
                    for _ in range(3):
                        if not parent:
                            break
                        candidatos.append(parent.ele("tag:input", timeout=0))
                        parent = parent.parent()
        for campo in candidatos:
            if (campo and campo.states.is_displayed
                    and campo.attr("readonly") is None
                    and campo.attr("disabled") is None
                    and campo.attr("idsmask") != "currency"):
                return campo
        restante = limite - time.monotonic()
        if restante <= 0:
            return None
        time.sleep(min(0.1, restante))

def _ler_valor_cpf(tab, campo) -> str:
    try:
        valor = campo.value or campo.attr("value") or ""
    except Exception:
        valor = ""

    if not valor:
        try:
            valor = tab.run_js("return arguments[0].value || '';", campo) or ""
        except Exception:
            valor = ""

    return re.sub(r"\D", "", str(valor))

def _setar_valor_cpf_angular(tab, campo, cpf: str) -> bool:
    """Fallback para Angular Material quando o input visual nao sincroniza."""
    try:
        tab.run_js(
            """
            const input = arguments[0];
            const value = arguments[1];
            const setter = Object.getOwnPropertyDescriptor(
              window.HTMLInputElement.prototype,
              'value'
            ).set;
            setter.call(input, value);
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            input.dispatchEvent(new Event('blur', { bubbles: true }));
            """,
            campo,
            cpf,
        )
        time.sleep(0.4)
        return _ler_valor_cpf(tab, campo) == cpf
    except Exception as exc:
        log.warning(f"Credline: fallback Angular falhou: {exc}")
        return False

def _digitar_cpf(tab, campo, cpf: str) -> bool:
    """Digita CPF usando input nativo DrissionPage com validação de máscara e fallbacks."""
    try:
        campo.input(cpf, clear=True)
        time.sleep(0.1)

        valor_digitado = _ler_valor_cpf(tab, campo)
        if valor_digitado == cpf:
            return True

        if _setar_valor_cpf_angular(tab, campo, cpf):
            return True

        log.info(f"Credline: digitando CPF caractere a caractere...")
        campo.input("", clear=True)
        campo.click()
        for char in cpf:
            tab.actions.type(char)
            time.sleep(0.02)

        return _ler_valor_cpf(tab, campo) == cpf
    except Exception as exc:
        log.error(f"Erro ao digitar CPF: {exc}")
        return False

def consultar_credline_cpf(page, cpf: str, placa: str = "", valor_veiculo: str = "") -> Dict[str, Any]:
    """
    Consulta Adaptativa no Itaú Credline seguindo o fluxo confiável do MODELO BASE:
    - Preenchimento do CPF com validação de máscara Angular.
    - Polling inteligente com normalização de texto sem acentos.
    - Simulação por placa completa em caso de aprovação.
    """
    tab = _pegar_aba(page, "new-simulator", CREDLINE_URL)

    try:
        # Sessão já pronta: não repetir a busca de treze campos de login.
        if not _achar_campo_cpf(tab, timeout=0):
            _garantir_login_credline(tab)

        if "new-simulator" not in tab.url.lower() and "login" not in tab.url.lower() and "auth" not in tab.url.lower():
            tab.get(CREDLINE_URL)
            time.sleep(0.8)

        # 1. Limpar consulta anterior se houver
        limpar_btn = tab.ele("@class:clear-button", timeout=0) or tab.ele('text=Limpar', timeout=0) or tab.ele('xpath://button[contains(., "Limpar") or contains(., "limpar")]', timeout=0)
        if limpar_btn and limpar_btn.states.is_displayed:
            try:
                limpar_btn.click()
            except Exception:
                tab.run_js('const b = document.querySelector(".clear-button") || Array.from(document.querySelectorAll("button")).find(el => el.textContent.includes("Limpar")); if(b) b.click();')
            time.sleep(0.2)

        campo = _achar_campo_cpf(tab, timeout=2.0)
        if not campo:
            log.info("    [CREDLINE] Campo CPF não encontrado na tela atual. Recarregando simulador...")
            tab.get(CREDLINE_URL)
            time.sleep(1.5)
            campo = _achar_campo_cpf(tab, timeout=4.0)

        if not campo:
            return {"status": "PENDENTE ITAU", "financed_value": None, "observation": "Campo CPF não encontrado"}

        if not _digitar_cpf(tab, campo, cpf):
            return {"status": "PENDENTE ITAU", "financed_value": None, "observation": "Falha ao digitar CPF no simulador"}

        botao = tab.ele("#continuar-button", timeout=3) or tab.ele('xpath://button[@id="continuar-button"]', timeout=2)
        if botao:
            try:
                botao.hover()
                time.sleep(0.1)
                botao.click()
            except Exception:
                tab.run_js('if(document.getElementById("continuar-button")) document.getElementById("continuar-button").click();')
        else:
            tab.run_js('if(document.getElementById("continuar-button")) document.getElementById("continuar-button").click();')

        # POLLING ADAPTATIVO FLUIDO
        start_time = time.time()
        max_wait_seconds = 35.0
        decisao = None
        motivo_final = ""

        while time.time() - start_time < max_wait_seconds:
            elapsed = time.time() - start_time
            check_interval = 0.25

            body = tab.ele("tag:body", timeout=0.2)
            texto_original = body.text if body else ""
            texto_norm = normalizar_texto(texto_original)

            if "carregando" in texto_norm or "aguarde" in texto_norm or "loading" in texto_norm:
                time.sleep(check_interval)
                continue

            if not texto_norm or len(texto_norm) < 40:
                time.sleep(check_interval)
                continue

            erro_vermelho = tab.ele("tag:mat-error", timeout=0) or tab.ele(".mat-error", timeout=0) or tab.ele(".text-danger", timeout=0)
            if erro_vermelho and erro_vermelho.text:
                log.info(f"Credline: erro visual detectado: {erro_vermelho.text[:60]}")
                return {"status": "PENDENTE ITAU", "financed_value": None, "observation": f"Erro visual: {erro_vermelho.text[:100]}"}

            aprovado, dec, mot = _decidir_credline(texto_norm)
            if mot != "resultado nao reconhecido":
                decisao = dec
                motivo_final = mot
                log.info(f"Credline: Decisão '{decisao}' obtida em {elapsed:.1f}s ({motivo_final})")
                break

            time.sleep(check_interval)

        if not decisao:
            log.warning(f"Credline: Timeout aguardando resultado para CPF {cpf}")
            return {"status": "PENDENTE ITAU", "financed_value": None, "observation": "Timeout aguardando resultado do Itaú"}

        if decisao == "REPROVADO":
            log.info(f"[CREDLINE] CPF {cpf} ➔ ❌ REPROVADO (Sem crédito aprovável Itaú)")
            return {"status": "REPROVADO", "financed_value": None, "observation": "Reprovado - Sem crédito aprovável Itaú"}

        if decisao == "PENDENTE ITAU":
            return {"status": "PENDENTE ITAU", "financed_value": None, "observation": motivo_final}

        # CLIENTE APROVADO (NAO CORRENTISTA OU CORRENTISTA)
        valor_financiado = None
        if placa and valor_veiculo:
            try:
                log.info(f"[CREDLINE] CPF {cpf} ➔ Aprovado como '{decisao}'. Simulando veículo ({placa} / R$ {valor_veiculo})...")
                valor_financiado = simular_placa_credline(tab, placa, valor_veiculo)
            except Exception as exc:
                log.warning(f"Credline: Erro ao simular placa para CPF {cpf}: {exc}")

        if not valor_financiado or valor_financiado == "ERRO_SIMULACAO":
            valor_financiado = "SALDO INDISPONIVEL"

        obs = f"Aprovado - {decisao} | Financiado: {valor_financiado}"
        log.info(f"[CREDLINE] CPF {cpf} ➔ 🎯 RESULTADO FINAL: {decisao} | Saldo: {valor_financiado}")

        return {
            "status": decisao,
            "financed_value": valor_financiado,
            "observation": obs
        }

    except Exception as exc:
        log.error(f"Erro na consulta Credline ({cpf}): {exc}")
        return {"status": "PENDENTE ITAU", "financed_value": None, "observation": f"Erro interno: {exc}"}

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
            log.info("[CREDLINE] Checker 'Veículo Usado' marcado com sucesso.")
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
                    log.info(f"[CREDLINE] Checker 'Veículo Usado' marcado via seletor: {sel}")
                    return True
            except Exception:
                continue

    except Exception as exc:
        log.debug(f"[CREDLINE] Verificação de veículo usado: {exc}")
    return False

def _preencher_valor_entrada(tab) -> bool:
    """Preenche e confere a entrada obrigatória na etapa do veículo."""
    campo = tab.ele(
        'css:input[formcontrolname="entryValue"], input[data-cy="entry-value"]',
        timeout=5,
    )
    if not campo or not campo.states.is_displayed:
        return False
    campo.input(VALOR_ENTRADA, clear=True)
    tab.run_js("""
        const el = arguments[0];
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.blur();
        el.dispatchEvent(new Event('blur', { bubbles: true }));
    """, campo)
    limite = time.monotonic() + 2
    while True:
        valor = re.sub(r"\D", "", str(campo.value or "")).lstrip("0")
        if valor == "3000000" and campo.attr("aria-invalid") != "true":
            log.info("[CREDLINE] Entrada conferida: R$ %s", VALOR_ENTRADA)
            return True
        if time.monotonic() >= limite:
            return False
        time.sleep(0.1)


def simular_placa_credline(tab, placa: str, valor_veiculo: str) -> Optional[str]:
    """
    Executa a simulação por placa com todos os passos comprovados do MODELO BASE.
    """
    try:
        # Fechar modal informativo de pré-aprovado se aparecer
        try:
            modal_fechar = tab.ele('xpath://button[contains(., "fechar")]', timeout=2) or tab.ele('xpath://button[contains(translate(., "FECHAR", "fechar"), "fechar")]', timeout=1)
            if modal_fechar:
                modal_fechar.click()
                log.info("    [CREDLINE] Modal informativo fechado.")
                time.sleep(0.5)
        except Exception:
            pass

        tab.scroll.to_bottom()
        time.sleep(0.5)
        _marcar_veiculo_usado(tab)

        # 1. Clicar na Tab "Placa"
        btn_placa = tab.ele('xpath://button[@role="tab" and contains(., "Placa")]', timeout=5)
        if not btn_placa:
            btn_placa = tab.ele('text=Placa', timeout=3)
        if btn_placa:
            btn_placa.click()
            log.info("    [CREDLINE] Tab Placa selecionada.")
        else:
            log.warning("    [CREDLINE] Não foi possível encontrar a aba Placa. Simulação pulada.")
            return "ERRO_SIMULACAO"

        time.sleep(1.0)
        _marcar_veiculo_usado(tab)

        # 2. Inserir a Placa do Veículo
        input_placa = tab.ele('@data-cy=vehicle-identification', timeout=5)
        if not input_placa:
            input_placa = tab.ele('xpath://input[@data-cy="vehicle-identification"]', timeout=3)
        if not input_placa:
            input_placa = tab.ele('input[formcontrolname="vehicleIdentification"]', timeout=3)
        if input_placa:
            input_placa.input(placa, clear=True)
            log.info(f"    [CREDLINE] Placa preenchida: {placa}")
        else:
            log.warning("    [CREDLINE] Campo de placa não encontrado. Simulação pulada.")
            return "ERRO_SIMULACAO"

        time.sleep(0.5)

        # 3. Clicar em "Buscar veículo"
        btn_buscar = tab.ele('xpath://button[contains(., "Buscar veículo")]', timeout=5)
        if btn_buscar:
            btn_buscar.click()
            log.info("    [CREDLINE] Botão Buscar veículo clicado.")
        else:
            tab.run_js("const btn = Array.from(document.querySelectorAll('button')).find(el => el.textContent.includes('Buscar veículo')); if(btn) btn.click();")
            log.info("    [CREDLINE] Botão Buscar veículo acionado via JS.")

        time.sleep(1.5)

        # 4. Selecionar o Veículo Radio Button
        radio_veiculo = tab.ele('xpath://input[@type="radio" and @formcontrolname="vehicle"]', timeout=10)
        if not radio_veiculo:
            radio_veiculo = tab.ele('input[type="radio"][formcontrolname="vehicle"]', timeout=5)
        if radio_veiculo:
            radio_veiculo.click()
            log.info("    [CREDLINE] Veículo retornado foi selecionado (Radio Button).")
        else:
            log.warning("    [CREDLINE] Não apareceu o rádio button do veículo após busca de placa. Simulação pulada.")
            return "ERRO_SIMULACAO"

        time.sleep(0.5)
        _marcar_veiculo_usado(tab)

        # 5. Inserir o Valor do Veículo (Bypass de máscara de moeda via JS robusto)
        log.info(f"    [CREDLINE] Injetando valor do veículo: R$ {valor_veiculo}")
        valor_sanitizado = re.sub(r"[^0-9.,]", "", valor_veiculo)
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

        if not _preencher_valor_entrada(tab):
            log.warning("[CREDLINE] Entrada de R$ 30.000,00 ausente ou inválida. Simulação interrompida.")
            return "ERRO_SIMULACAO"

        # 6. Clicar em "Simular financiamento"
        btn_simular = tab.ele('@data-cy=create-simulation-button', timeout=5)
        if not btn_simular:
            btn_simular = tab.ele('xpath://button[@data-cy="create-simulation-button"]', timeout=3)
        if btn_simular:
            btn_simular.click()
            log.info("    [CREDLINE] Botão Simular financiamento clicado.")
        else:
            tab.run_js("const btn = document.querySelector('button[data-cy=\"create-simulation-button\"]'); if(btn) btn.click();")
            log.info("    [CREDLINE] Botão Simular financiamento acionado via JS.")

        # 7. Aguardar e extrair o "Valor Financiado Disponível"
        log.info("    [CREDLINE] Aguardando carregamento da simulação de financiamento (até 15s)...")
        xpath_financiado = 'xpath://p[contains(text(), "Valor à financiar") or contains(text(), "Valor a financiar")]/parent::div/following-sibling::div/p'
        val_el = tab.ele(xpath_financiado, timeout=15)

        if not val_el:
            val_els = tab.eles('@data-cy=vehicle-value-description')
            if not val_els:
                val_els = tab.eles('xpath://p[@data-cy="vehicle-value-description"]')
            if val_els:
                val_el = val_els[1] if len(val_els) >= 2 else val_els[0]

        if val_el:
            valor_financiado = val_el.text.strip()
            log.info(f"    >>> SIMULAÇÃO CONCLUÍDA! Valor Financiado Disponível: {valor_financiado} <<<")
            return valor_financiado
        else:
            log.warning("    [CREDLINE] Elemento do valor financiado não apareceu (timeout).")
            return "ERRO_SIMULACAO"
    except Exception as exc:
        log.warning(f"    [CREDLINE] Erro durante simulação por placa: {exc}")
        return "ERRO_SIMULACAO"
