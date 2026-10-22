from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.automation import credline_scraper as scraper


def elemento(value="", attrs=None):
    attrs = attrs or {}
    campo = Mock()
    campo.value = value
    campo.attr.side_effect = attrs.get
    campo.states.is_displayed = True
    campo.input.side_effect = lambda texto, clear=True: setattr(campo, "value", texto)
    return campo


@pytest.fixture
def relogio(monkeypatch):
    agora = [0.0]
    monkeypatch.setattr(scraper.time, "monotonic", lambda: agora[0])
    monkeypatch.setattr(scraper.time, "sleep", lambda segundos: agora.__setitem__(0, agora[0] + segundos))
    return agora


def test_entrada_preenche_valor_e_dispara_eventos():
    campo = elemento("0,00")
    tab = Mock()
    tab.ele.return_value = campo
    assert scraper._preencher_valor_entrada(tab)
    campo.input.assert_called_once_with("30.000,00", clear=True)
    script, alvo = tab.run_js.call_args.args
    assert alvo is campo
    for evento in ("input", "change", "blur"):
        assert f"Event('{evento}'" in script


@pytest.mark.parametrize("valor,attrs", [
    ("300,00", {}),
    ("30.000,00", {"aria-invalid": "true"}),
])
def test_entrada_incorreta_ou_invalida_bloqueia(valor, attrs, relogio):
    campo = elemento(valor, attrs)
    campo.input.side_effect = None  # Simula máscara rejeitando/invalidando o valor.
    tab = Mock()
    tab.ele.return_value = campo
    assert not scraper._preencher_valor_entrada(tab)
    assert relogio[0] >= 2


def test_entrada_ausente():
    tab = Mock()
    tab.ele.return_value = None
    assert not scraper._preencher_valor_entrada(tab)
    tab.run_js.assert_not_called()


def test_cpf_usa_um_unico_prazo(relogio):
    tab = Mock()
    tab.eles.return_value = []
    assert scraper._achar_campo_cpf(tab, timeout=2) is None
    assert relogio[0] == pytest.approx(2)
    assert all(call.kwargs["timeout"] == 0 for call in tab.eles.call_args_list)


def test_cpf_nao_confunde_moeda_ou_campo_bloqueado():
    moeda = elemento(attrs={"idsmask": "currency"})
    bloqueado = elemento(attrs={"readonly": ""})
    cpf = elemento()
    tab = Mock()
    tab.eles.side_effect = lambda sel, timeout: [moeda, bloqueado, cpf] if sel.startswith("css:") else []
    assert scraper._achar_campo_cpf(tab, timeout=0) is cpf


def test_fallback_cpf_nao_retorna_sucesso_sem_validar(monkeypatch, relogio):
    campo = elemento("111")
    campo.input.side_effect = None
    monkeypatch.setattr(scraper, "_setar_valor_cpf_angular", lambda *args: False)
    assert not scraper._digitar_cpf(Mock(), campo, "12345678901")
    campo.click.assert_called_once()


def test_consultas_seguidas_sem_esperas_de_login_ou_entrada(monkeypatch, relogio):
    campo = elemento()
    tab = Mock()
    tab.url = scraper.CREDLINE_URL
    botao = Mock()
    limpar = elemento()
    body = SimpleNamespace(text="Resultado da análise de crédito: não temos condições aprováveis para esta consulta.")
    tab.ele.side_effect = lambda sel, timeout=0: {
        "@class:clear-button": limpar,
        "#continuar-button": botao,
        "tag:body": body,
    }.get(sel)
    monkeypatch.setattr(scraper, "_pegar_aba", lambda *args: tab)
    monkeypatch.setattr(scraper, "_achar_campo_cpf", lambda *args, **kwargs: campo)
    login = Mock()
    monkeypatch.setattr(scraper, "_garantir_login_credline", login)
    for cpf in ("12345678901", "98765432100"):
        assert scraper.consultar_credline_cpf(Mock(), cpf)["status"] == "REPROVADO"
        assert campo.value == cpf
    login.assert_not_called()
    assert limpar.click.call_count == 2
    assert relogio[0] < 1
    assert not any("entryValue" in call.args[0] for call in tab.ele.call_args_list)


@pytest.mark.parametrize("entrada_valida", [True, False])
def test_simulacao_so_clica_apos_preencher_entrada(monkeypatch, relogio, entrada_valida):
    tab = Mock()
    botao = Mock()
    resultado = SimpleNamespace(text="R$ 120.000,00")
    eventos = []
    tab.ele.side_effect = lambda sel, timeout=0: (
        botao if sel == "@data-cy=create-simulation-button"
        else resultado if "Valor à financiar" in sel
        else elemento()
    )
    botao.click.side_effect = lambda: eventos.append("simular")
    monkeypatch.setattr(scraper, "_marcar_veiculo_usado", lambda tab: True)
    def preencher(tab):
        eventos.append("entrada")
        return entrada_valida
    monkeypatch.setattr(scraper, "_preencher_valor_entrada", preencher)
    retorno = scraper.simular_placa_credline(tab, "ABC1D23", "150.000,00")
    assert eventos == (["entrada", "simular"] if entrada_valida else ["entrada"])
    assert retorno == ("R$ 120.000,00" if entrada_valida else "ERRO_SIMULACAO")
