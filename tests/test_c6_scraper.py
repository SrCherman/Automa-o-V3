from unittest.mock import Mock

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


@pytest.mark.parametrize("text", ["Erro de rede", "Valor sugerido\nFinanciamento R$ 0,00", "Valor sugerido\nParcela R$ 3.000,00"])
def test_unknown_results_never_become_negative_or_approved(text):
    assert c6.extrair_resultado_c6(text) is None


@pytest.mark.parametrize("value,expected", [("R$ 117.000,00", "117000.00"), ("130000", "130000"), ("0,00", "0.00")])
def test_currency(value, expected):
    assert str(c6.valor_monetario(value)) == expected


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


@pytest.mark.parametrize("entry", ["13.000,00", ""])
def test_submit_happens_after_validated_fields_and_callback(monkeypatch, entry):
    tab = Mock()
    tab.url = c6.C6_URL
    page = Mock()
    page.get_tabs.return_value = [tab]
    cnh = Mock()
    cnh.attr.return_value = "true"
    model = Mock(value="Veículo preenchido")
    entry_field = Mock(value="")
    tab.ele.side_effect = lambda sel, timeout=0: cnh if "CNH" in sel else entry_field if "valorDeEntrada" in sel else model
    monkeypatch.setattr(c6, "iniciar_nova_consulta", lambda _: None)
    monkeypatch.setattr(c6, "check_and_solve_cloudflare", lambda _: True)
    monkeypatch.setattr(c6, "_select_uf", lambda *a: None)
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
    monkeypatch.setattr(c6, "_body", lambda _: "Valor sugerido\nFinanciamento R$ 117.000,00")
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
