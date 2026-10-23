"""Consulta C6 Auto até o valor sugerido de financiamento, sem avançar a proposta."""

import logging
import re
import time
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from src.config import C6_URL, C6_USER, C6_PASS
from src.automation.mind7_scraper import normalizar_nascimento, _idade
from src.automation.cloudflare_solver import check_and_solve_cloudflare

log = logging.getLogger("orchestrator.c6")
C6_FORM_TIMEOUT = 20
C6_OPTION_TIMEOUT = 6
C6_VEHICLE_TIMEOUT = 90
C6_RESULT_TIMEOUT = 180


class C6Unavailable(RuntimeError):
    """Portal, autenticação ou seleção do veículo precisa de atenção."""


def valor_monetario(value):
    text = re.sub(r"[^\d,.-]", "", str(value or ""))
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        result = Decimal(text)
        if not result.is_finite() or result < 0 or result.as_tuple().exponent < -2:
            raise ValueError("Valor monetário inválido")
        return result
    except InvalidOperation as exc:
        raise ValueError("Valor monetário inválido") from exc


def normalizar_celular_c6(value):
    raw = str(value or "").strip()
    if re.fullmatch(r"\d+\.0+", raw):
        raw = raw.split(".", 1)[0]
    phone = re.sub(r"\D", "", raw)
    if phone.startswith("55") and len(phone) in (12, 13):
        phone = phone[2:]
    # Telefones legados de 10 dígitos com assinante 8/9 são celulares pré-migração.
    if len(phone) == 10 and phone[2] in "89":
        phone = phone[:2] + "9" + phone[2:]
    if len(phone) != 11 or phone[2] != "9":
        raise ValueError("Celular inválido para o C6")
    return phone


def extrair_resultado_c6(text):
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    plain = re.sub(r"\s+", " ", plain)
    if re.search(r"\bsimulando\b|selecione\s+um\s+valor\s+permitido", plain):
        return None
    if re.search(r"proposta nao atende (?:aos )?requisitos|fora da politica", plain):
        return {"status": "FORA DA POLITICA", "financed_value": None,
                "observation": "C6: proposta não atende aos requisitos / fora da política"}
    if "cpf invalido" in plain or "cpf incompativel" in plain:
        return {"status": "DADOS INVALIDOS", "financed_value": None, "observation": "C6: confira o CPF informado"}
    if re.search(r"\berro\b", plain):
        return None
    if "valor sugerido" in plain:
        match = re.search(r"\bvalor\s+sugerido\b[\s\S]*?\bfinanciamento\s*R\$\s*([\d.]+,\d{2})", text, re.I)
        if match and valor_monetario(match.group(1)) > 0:
            return {"status": "APROVADO", "financed_value": f"R$ {match.group(1)}",
                    "observation": "Valor sugerido de financiamento exibido pelo C6"}
    return None


def _wait(predicate, seconds=20, stage=None):
    started = time.monotonic()
    deadline = started + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            elapsed = time.monotonic() - started
            if stage and elapsed >= 3:
                log.info("[C6] carregamento=%s concluido_em=%.1fs", stage, elapsed)
            return result
        time.sleep(0.1)
    if stage:
        log.warning("[C6] carregamento=%s timeout_apos=%ss", stage, seconds)
    return None


def _body(tab):
    body = tab.ele("tag:body", timeout=0)
    return body.raw_text if body else ""


def _click_text(tab, text):
    """Clique via JS: botões Material ficam sob overlays e o clique nativo falha."""
    return bool(tab.run_js(
        "const b=[...document.querySelectorAll('button')]"
        f".find(b=>b.innerText.trim().toUpperCase()==={text.upper()!r});"
        "if(b){b.click();return true} return false"))


def _button(tab, text):
    return tab.ele(f'xpath://button[translate(normalize-space(.), "abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")="{text.upper()}"]', timeout=0)


def _login(tab):
    if urlparse(tab.url).hostname != "c6auto.com.br":
        raise C6Unavailable("C6 redirecionou para um domínio não reconhecido")
    start = tab.ele("#submit-button", timeout=0)
    if start:
        start.click()
        if not _wait(lambda: "/shopkeeper-panel" in tab.url or tab.ele("#username", timeout=0),
                     C6_FORM_TIMEOUT, "tela de login"):
            raise C6Unavailable("C6 não exibiu o formulário de login")
    user = tab.ele("#username", timeout=0)
    if user:
        if urlparse(tab.url).hostname != "c6auto.com.br" or not C6_USER or not C6_PASS:
            raise C6Unavailable("Configure C6_USER e C6_PASS no .env ou faça o login no navegador")
        user.input(C6_USER, clear=True)
        password = tab.ele("#password", timeout=2)
        if not password:
            raise C6Unavailable("Campo de senha C6 indisponível")
        password.input(C6_PASS, clear=True)
        tab.ele("#kc-login", timeout=2).click()
    if not _wait(lambda: urlparse(tab.url).hostname == "c6auto.com.br" and "/shopkeeper-panel" in tab.url,
                 C6_FORM_TIMEOUT, "retorno do login"):
        raise C6Unavailable("Login C6 pendente; verifique a aba do navegador")


def iniciar_nova_consulta(tab):
    """Abre um formulário limpo reaproveitando a aba, sem reload quando possível."""
    if "pre-analysis" in tab.url or not _wait(
            lambda: _button(tab, "CRIAR UMA NOVA PROPOSTA"), 1.0):
        tab.get(C6_URL)
    if not _wait(lambda: _button(tab, "CRIAR UMA NOVA PROPOSTA") or tab.ele("#submit-button", timeout=0) or tab.ele("#username", timeout=0),
                 C6_FORM_TIMEOUT, "painel C6"):
        raise C6Unavailable("Painel C6 indisponível")
    time.sleep(0.4)  # deixa o router do painel terminar antes de clicar
    if "/shopkeeper-panel" not in tab.url:
        _login(tab)
    if urlparse(tab.url).hostname != "c6auto.com.br":
        raise C6Unavailable("C6 redirecionou para um domínio não reconhecido")
    field = None
    for _ in range(3):
        create = _wait(lambda: _button(tab, "CRIAR UMA NOVA PROPOSTA"), 5, "nova proposta")
        if not create:
            break
        _click_text(tab, "CRIAR UMA NOVA PROPOSTA")
        time.sleep(0.6)  # Angular liga o handler do router depois de pintar o botão
        # O CPF aparece antes de o C6 terminar de carregar produto, UF e placa.
        field = _wait(lambda: tab.ele('css:input[formcontrolname="numeroDocumento"]', timeout=0),
                      8, "formulario da proposta")
        if field:
            break
    if not field or re.sub(r"\D", "", field.value or ""):
        raise C6Unavailable("O C6 não abriu um formulário novo e vazio")


def _input(tab, control, value):
    field = _wait(lambda: tab.ele(f'css:input[formcontrolname="{control}"]', timeout=0),
                  C6_FORM_TIMEOUT, f"campo {control}")
    if not field:
        raise C6Unavailable(f"Campo C6 indisponível: {control}")
    if control in ("valorDoVeiculo", "valorDeEntrada"):
        field.run_js("this.focus(); this.select();")
        field.input(tuple(str(int(valor_monetario(value) * 100))))
    else:
        field.input(value, clear=True)
    field.run_js("this.dispatchEvent(new Event('change', {bubbles:true})); this.blur();")
    return field


def _select_uf(tab, uf):
    select = _wait(lambda: tab.ele('css:mat-select[formcontrolname="ufDeLicenciamento"]', timeout=0),
                   C6_FORM_TIMEOUT, "campo de UF")
    if not select:
        raise C6Unavailable("Campo UF C6 indisponível")
    if select.text.strip().upper() != uf:
        select.click()
        option = _wait(lambda: tab.ele(f'xpath://mat-option[normalize-space(.)="{uf}"]', timeout=0),
                       C6_OPTION_TIMEOUT, "opcoes de UF")
        if not option:
            raise C6Unavailable("UF não disponível no C6")
        option.click()
    if not _wait(lambda: select.text.strip().upper() == uf, C6_OPTION_TIMEOUT, "confirmacao da UF"):
        raise ValueError("C6 não confirmou a UF selecionada")


def _select_vehicle_type(tab, vehicle_type):
    def label(value):
        return unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().strip().upper()

    select = _wait(lambda: tab.ele('css:mat-select[formcontrolname="tipoDeVeiculo"]', timeout=0),
                   C6_FORM_TIMEOUT, "campo de tipo de veiculo")
    if not select:
        raise C6Unavailable("Campo de tipo de veículo C6 indisponível")
    expected = label(vehicle_type)
    # Enquanto o Angular carrega o texto fica vazio: espera curta, depois mantém o padrão.
    atual = _wait(lambda: label(select.raw_text), 5) or ""
    if atual and atual != expected:
        option = None
        for _ in range(2):
            select.click()
            option = _wait(lambda: next((o for o in tab.eles('tag:mat-option')
                                         if label(o.raw_text) == expected), None),
                           C6_OPTION_TIMEOUT, "opcoes de tipo de veiculo")
            if option:
                break
        if not option:
            raise C6Unavailable("Tipo de veículo não disponível no C6")
        option.click()
    if atual and not _wait(lambda: label(select.raw_text) == expected,
                           C6_OPTION_TIMEOUT, "confirmacao do tipo de veiculo"):
        raise ValueError("C6 não confirmou o tipo de veículo")


def consultar_c6_cpf(page, cpf, phone, birth_date, plate, vehicle_value, options, before_submit):
    cpf = re.sub(r"\D", "", cpf or "")
    phone = normalizar_celular_c6(phone)
    nascimento = normalizar_nascimento(birth_date)
    plate = re.sub(r"[^A-Z0-9]", "", (plate or "").upper())
    uf = str(options.get("uf", "")).upper()
    if len(cpf) != 11 or len(set(cpf)) == 1 or not nascimento or len(plate) != 7:
        raise ValueError("CPF, nascimento ou placa inválidos para o C6")
    if int(_idade(nascimento) or 0) > 75:
        return {"status": "FORA DA POLITICA", "financed_value": None,
                "observation": "C6: cliente acima de 75 anos (limite do portal)"}
    if uf not in "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split():
        raise ValueError("UF inválida para o C6")
    if options.get("has_cnh") not in (True, False, 0, 1):
        raise ValueError("CNH deve ser confirmada antes da consulta")
    vehicle = valor_monetario(vehicle_value)
    entry_text = options.get("entry_value", "")
    entry = valor_monetario(entry_text) if entry_text else None
    if vehicle <= 0 or (entry is not None and entry >= vehicle):
        raise ValueError("Entrada deve ser menor que o valor do veículo")

    tabs = page.get_tabs()
    tab = next((t for t in tabs if urlparse(t.url).hostname == "c6auto.com.br"), None) or page.new_tab(C6_URL)
    iniciar_nova_consulta(tab)
    if not check_and_solve_cloudflare(tab):
        raise C6Unavailable("C6 aguarda validação no navegador")
    _select_vehicle_type(tab, options.get("vehicle_type", "VEÍCULOS LEVES"))
    fields = {
        "numeroDocumento": cpf,
        "celular": phone,
        "dataDeNascimento": date.fromisoformat(nascimento).strftime("%d/%m/%Y"),
    }
    for control, value in fields.items():
        field = _input(tab, control, value)
        if re.sub(r"\D", "", field.value or "") != re.sub(r"\D", "", value) or field.attr("aria-invalid") == "true":
            erro = ""
            try:
                bloco = field.ele("xpath:ancestor::*/descendant::mat-error", timeout=0.3)
                erro = (bloco.text or "").strip() if bloco else ""
            except Exception:
                pass
            raise ValueError(f"C6 rejeitou o campo {control}{': ' + erro if erro else ''}")
    cnh = _wait(lambda: tab.ele('xpath://mat-checkbox[contains(.,"Cliente possui CNH")]//input', timeout=0),
                C6_FORM_TIMEOUT, "campo de CNH")
    if not cnh:
        raise C6Unavailable("Campo de CNH indisponível")
    if cnh.states.is_checked != bool(options["has_cnh"]):
        tab.ele('xpath://mat-checkbox[contains(.,"Cliente possui CNH")]//label', timeout=2).click()
    if cnh.states.is_checked != bool(options["has_cnh"]):
        raise ValueError("C6 não confirmou a informação de CNH")
    _select_uf(tab, uf)
    plate_field = _input(tab, "plateNumber", plate)
    if re.sub(r"[^A-Z0-9]", "", (plate_field.value or "").upper()) != plate:
        raise ValueError("C6 não confirmou a placa")

    def vehicle_ready():
        return all((field := tab.ele(f'css:input[formcontrolname="{control}"]', timeout=0)) and field.value
                   for control in ("marca", "anoModelo", "modelo"))

    if not _wait(vehicle_ready, C6_VEHICLE_TIMEOUT, "consulta da placa"):
        raise C6Unavailable("A placa não preencheu marca, ano e modelo; confira o veículo no C6")
    amounts = [("valorDoVeiculo", vehicle_value)]
    if entry is not None:
        amounts.append(("valorDeEntrada", entry_text))
    for control, value in amounts:
        field = _input(tab, control, str(value))
        expected = vehicle if control == "valorDoVeiculo" else entry
        if valor_monetario(field.value) != expected or field.attr("aria-invalid") == "true":
            raise ValueError(f"C6 não confirmou o valor do campo {control}")
    if entry is None:
        field = _wait(lambda: tab.ele('css:input[formcontrolname="valorDeEntrada"]', timeout=0),
                      C6_FORM_TIMEOUT, "campo de entrada")
        if not field:
            raise C6Unavailable("Campo de entrada C6 indisponível")
        if field.value:
            _input(tab, "valorDeEntrada", "")
            if field.value and valor_monetario(field.value) != 0:
                raise ValueError("C6 não permitiu deixar a entrada em branco")
    simulate = _wait(lambda: (b := _button(tab, "SIMULAR")) and b.attr("disabled") is None and b,
                     C6_FORM_TIMEOUT, "validacao do formulario")
    if not simulate:
        raise C6Unavailable("Formulário C6 inválido ou botão Simular indisponível")
    if extrair_resultado_c6(_body(tab)):
        raise C6Unavailable("C6 ainda exibe um resultado anterior; abra uma nova proposta antes de consultar")
    before_submit()
    _click_text(tab, "SIMULAR")
    result = _wait(lambda: extrair_resultado_c6(_body(tab)), C6_RESULT_TIMEOUT, "resultado da simulacao")
    if not result:
        raise TimeoutError("C6 recebeu a consulta, mas o resultado não foi confirmado. Confira a aba antes de repetir.")
    return result
