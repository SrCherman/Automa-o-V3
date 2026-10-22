from unittest.mock import Mock
from lxml import html

import pytest

from src.automation import c6_scraper as c6


def test_financing_value_uses_label_not_other_amounts():
    text = "2 de 3\nVeículo R$ 130.000,00\nEntrada R$ 13.000,00\nValor sugerido\nFinanciamento\nR$ 117.000,00\nParcela R$ 3.000,00"
    assert c6.extrair_resultado_c6(text)["financed_value"] == "R$ 117.000,00"
    assert c6.extrair_resultado_c6("Financiamento\nValor do Veículo\nR$ 130.000,00") is None


@pytest.mark.parametrize("message", ["Simulando...", "Selecione um valor permitido", "Selecione um\nvalor permitido",
                                     "Erro de simulação", "ERRO DE REDE", "Simulando...\nSelecione um valor permitido"])
def test_pending_or_failed_simulation_never_accepts_provisional_value(message):
    text = f"2 de 3\nValor sugerido\nFinanciamento\nR$ 117.000,00\n{message}"
    assert c6.extrair_resultado_c6(text) is None


def test_outside_policy_is_not_invalid_cpf_and_has_no_value():
    result = c6.extrair_resultado_c6("Proposta não atende requisitos\nFora da Política")
    assert result["status"] == "FORA DA POLITICA"
    assert result["financed_value"] is None


def test_cpf_incompatible_is_reported_even_with_error_heading():
    assert c6.extrair_resultado_c6("Erro\nCPF incompatível")["status"] == "DADOS INVALIDOS"


def test_approved_amount_must_follow_suggested_value_heading():
    text = "Financiamento R$ 13.000,00\nValor sugerido\nFinanciamento R$ 117.000,00"
    assert c6.extrair_resultado_c6(text)["financed_value"] == "R$ 117.000,00"


@pytest.mark.parametrize("text", ["Erro de rede", "Valor sugerido\nFinanciamento R$ 0,00", "Valor sugerido\nParcela R$ 3.000,00"])
def test_unknown_results_never_become_negative_or_approved(text):
    assert c6.extrair_resultado_c6(text) is None


@pytest.mark.parametrize("value,expected", [("R$ 117.000,00", "117000.00"), ("130000", "130000"), ("0,00", "0.00")])
def test_currency(value, expected):
    assert str(c6.valor_monetario(value)) == expected


@pytest.mark.parametrize("control,value,digits", [
    ("valorDoVeiculo", "130.000,00", "13000000"),
    ("valorDeEntrada", "13.000,00", "1300000"),
])
def test_currency_input_uses_mask_aware_key_entry(control, value, digits):
    tab = Mock()
    field = Mock(value="")
    tab.ele.return_value = field

    c6._input(tab, control, value)

    field.input.assert_called_once_with(tuple(digits))
    assert field.run_js.call_args_list[0].args[0] == "this.focus(); this.select();"


@pytest.mark.parametrize("value,expected", [
    ("4499150670", "44999150670"),
    ("(44) 8915-0670", "44989150670"),
    ("554499150670", "44999150670"),
    ("5544999150670", "44999150670"),
    ("44999150670", "44999150670"),
    ("44999150670.0", "44999150670"),
])
def test_normalizar_celular_c6(value, expected):
    assert c6.normalizar_celular_c6(value) == expected


@pytest.mark.parametrize("value", ["4423456789", "4473456789", "449891506700", "44899150670"])
def test_normalizar_celular_c6_rejects_fixed_or_malformed_numbers(value):
    with pytest.raises(ValueError, match="Celular inválido"):
        c6.normalizar_celular_c6(value)


@pytest.mark.parametrize("value", ["", "-1", "1,234", "NaN"])
def test_invalid_currency(value):
    with pytest.raises(ValueError):
        c6.valor_monetario(value)


def test_return_to_panel_checks_that_next_form_is_empty(monkeypatch):
    tab = Mock()
    tab.url = c6.C6_URL
    button = Mock()
    field = Mock(value="")
    monkeypatch.setattr(c6, "_button", lambda *a: button)
    tab.ele.return_value = field
    c6.iniciar_nova_consulta(tab)
    c6.iniciar_nova_consulta(tab)
    assert tab.get.call_count == button.click.call_count == 2
    field.value = "12345678901"
    with pytest.raises(c6.C6Unavailable, match="vazio"):
        c6.iniciar_nova_consulta(tab)


def test_create_proposal_button_matches_actual_title_case_dom():
    page = html.fromstring('<button><span> Criar uma nova proposta </span></button>')
    tab = Mock()
    tab.ele.side_effect = lambda selector, timeout=0: (page.xpath(selector.removeprefix('xpath:')) or [None])[0]
    assert c6._button(tab, "CRIAR UMA NOVA PROPOSTA") is not None


def test_new_proposal_retries_click_if_panel_does_not_navigate(monkeypatch):
    tab = Mock(url=c6.C6_URL)
    clicks = []
    create = Mock()
    create.click.side_effect = lambda: clicks.append(1)
    field = Mock(value="")
    monkeypatch.setattr(c6, "_button", lambda *args: create)
    monkeypatch.setattr(c6, "_wait", lambda predicate, seconds=20, stage=None: predicate())
    def element(selector, timeout=0):
        if 'plateNumber' in selector:
            return Mock() if len(clicks) == 2 else None
        if 'numeroDocumento' in selector:
            return field if len(clicks) == 2 else None
        return None
    tab.ele.side_effect = element
    c6.iniciar_nova_consulta(tab)
    assert len(clicks) == 2


def test_login_accepts_direct_sso_return_to_panel(monkeypatch):
    tab = Mock(url="https://c6auto.com.br/originacaolojista/login")
    start = Mock()
    start.click.side_effect = lambda: setattr(tab, "url", c6.C6_URL)
    tab.ele.side_effect = lambda selector, timeout=0: start if selector == "#submit-button" else None
    monkeypatch.setattr(c6, "_wait", lambda predicate, seconds=20, stage=None: predicate())
    c6._login(tab)
    start.click.assert_called_once()


def test_vehicle_type_is_selected_and_confirmed():
    tab = Mock()
    select = Mock(raw_text="")
    option = Mock(raw_text="VEÍCULOS LEVES")
    option.click.side_effect = lambda: setattr(select, "raw_text", "VEÍCULOS LEVES")
    tab.ele.return_value = select
    tab.eles.return_value = [option]
    c6._select_vehicle_type(tab, "VEÍCULOS LEVES")
    select.click.assert_called_once()
    option.click.assert_called_once()


def test_vehicle_type_retries_when_first_click_does_not_open_menu(monkeypatch):
    tab = Mock()
    select = Mock(raw_text="")
    option = Mock(raw_text="VEÍCULOS LEVES")
    option.click.side_effect = lambda: setattr(select, "raw_text", "VEÍCULOS LEVES")
    tab.ele.return_value = select
    tab.eles.side_effect = lambda selector: [option] if select.click.call_count == 2 else []
    monkeypatch.setattr(c6, "_wait", lambda predicate, seconds=20, stage=None: predicate())
    c6._select_vehicle_type(tab, "VEÍCULOS LEVES")
    assert select.click.call_count == 2
    option.click.assert_called_once()


@pytest.mark.parametrize("entry", ["13.000,00", ""])
@pytest.mark.parametrize("stale", [False, True])
def test_submit_happens_after_validated_fields_and_callback(monkeypatch, entry, stale):
    tab = Mock()
    tab.url = c6.C6_URL
    page = Mock()
    page.get_tabs.return_value = [tab]
    cnh = Mock()
    cnh.states.is_checked = True
    model = Mock(value="Veículo preenchido")
    entry_field = Mock(value="")
    tab.ele.side_effect = lambda sel, timeout=0: cnh if "CNH" in sel else entry_field if "valorDeEntrada" in sel else model
    monkeypatch.setattr(c6, "iniciar_nova_consulta", lambda _: None)
    monkeypatch.setattr(c6, "check_and_solve_cloudflare", lambda _: True)
    monkeypatch.setattr(c6, "_select_uf", lambda *a: None)
    monkeypatch.setattr(c6, "_select_vehicle_type", lambda *a: None)
    filled = []
    def field(tab, control, value):
        filled.append(control)
        f = Mock(value=value)
        f.attr.return_value = None
        return f
    monkeypatch.setattr(c6, "_input", field)
    button = Mock()
    button.attr.return_value = None
    events = []
    button.click.side_effect = lambda: events.append("click")
    monkeypatch.setattr(c6, "_button", lambda *a: button)
    monkeypatch.setattr(c6, "_body", lambda _: "Valor sugerido\nFinanciamento R$ 117.000,00" if stale or "click" in events else "")
    if stale:
        with pytest.raises(c6.C6Unavailable, match="resultado anterior"):
            c6.consultar_c6_cpf(page, "12345678901", "5545999990000", "1990-01-01", "ABC1D23", "130.000,00",
                                {"entry_value": entry, "uf": "PR", "has_cnh": True}, lambda: events.append("persist"))
        assert events == []
        return
    result = c6.consultar_c6_cpf(page, "12345678901", "5545999990000", "1990-01-01", "ABC1D23", "130.000,00",
                                {"entry_value": entry, "uf": "PR", "has_cnh": True}, lambda: events.append("persist"))
    assert events == ["persist", "click"]
    assert result["financed_value"] == "R$ 117.000,00"
    assert ("valorDeEntrada" in filled) == bool(entry)


def test_missing_data_never_opens_portal():
    page = Mock()
    with pytest.raises(ValueError):
        c6.consultar_c6_cpf(page, "12345678901", "45999990000", "", "ABC1D23", "130.000,00", {}, Mock())
    page.get_tabs.assert_not_called()


def test_c6_waits_longer_than_old_limit_and_logs_the_delay(monkeypatch, caplog):
    now = [0.0]
    monkeypatch.setattr(c6.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(c6.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    caplog.set_level("INFO", logger="orchestrator.c6")

    tab = Mock(url=c6.C6_URL)
    page = Mock()
    page.get_tabs.return_value = [tab]
    cnh = Mock()
    cnh.states.is_checked = True
    field = Mock(value="Loaded")
    entry = Mock(value="")
    def element(selector, timeout=0):
        if "CNH" in selector:
            return cnh
        if "valorDeEntrada" in selector:
            return entry
        return field
    tab.ele.side_effect = element
    monkeypatch.setattr(c6, "iniciar_nova_consulta", lambda _: None)
    monkeypatch.setattr(c6, "check_and_solve_cloudflare", lambda _: True)
    monkeypatch.setattr(c6, "_select_uf", lambda *args: None)
    monkeypatch.setattr(c6, "_select_vehicle_type", lambda *args: None)
    monkeypatch.setattr(c6, "_input", lambda _, __, value: Mock(value=value, **{"attr.return_value": None}))

    events = []
    button = Mock()
    button.attr.return_value = None
    button.click.side_effect = lambda: events.append("click")
    monkeypatch.setattr(c6, "_button", lambda *args: button)
    def body(_):
        if "click" not in events:
            return ""
        return "Simulando..." if now[0] < 60 else "Valor sugerido\nFinanciamento\nR$ 117.000,00"
    monkeypatch.setattr(c6, "_body", body)

    result = c6.consultar_c6_cpf(page, "12345678901", "5545999990000", "1990-01-01", "ABC1D23", "130.000,00",
                                 {"entry_value": "", "uf": "PR", "has_cnh": True}, lambda: None)

    assert now[0] >= 60
    assert result["financed_value"] == "R$ 117.000,00"
    assert "resultado da simulacao concluido_em=60.0s" in caplog.text
