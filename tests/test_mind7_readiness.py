from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.automation import mind7_scraper as mind7


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(mind7.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(mind7.time, "sleep", lambda seconds: setattr(clock, "now", clock.now + seconds))
    return clock


class ResultTab:
    def __init__(self, clock, response, *, busy_until=0, visible=True, message=""):
        self.clock = clock
        self.response = response
        self.busy_until = busy_until
        self.visible = visible
        self.message = message

    @property
    def html(self):
        raise AssertionError("Mensagens devem ser lidas do resultado visível, nunca do HTML")

    def ele(self, selector, timeout=0):
        if selector == "#botao-consultar":
            return SimpleNamespace(states=SimpleNamespace(is_enabled=self.clock.now >= self.busy_until))
        if selector == "#copiar_dados":
            return SimpleNamespace(value=self.response(self.clock.now))
        if selector == "#conteudo-retorno":
            return SimpleNamespace(raw_text=self.message,
                                   states=SimpleNamespace(is_displayed=self.visible))


def test_script_message_without_visible_response_times_out(clock):
    tab = ResultTab(clock, lambda _: "")
    with pytest.raises(TimeoutError):
        mind7._aguardar_resultado_mind7(tab, 3)


def test_hidden_no_data_template_does_not_end_query(clock):
    tab = ResultTab(clock, lambda _: "", visible=False, message="Nenhum registro encontrado")
    with pytest.raises(TimeoutError):
        mind7._aguardar_resultado_mind7(tab, 3)


def test_visible_no_data_waits_for_request_and_stable_message(clock):
    tab = ResultTab(clock, lambda _: "", busy_until=2, message="Nenhum registro encontrado")
    assert mind7._aguardar_resultado_mind7(tab, 5) is None
    assert clock.now >= 3


def test_partial_cpf_during_loading_waits_for_birth_date(clock):
    header = "CPF: 12345678901"
    complete = header + "\nNASCIMENTO: 01/01/1990"
    tab = ResultTab(clock, lambda now: header if now < 2 else complete, busy_until=2)
    result = mind7._aguardar_resultado_mind7(tab, 5)
    assert mind7.extrair_dados_cpf(result, "12345678901")["birth_date"] == "1990-01-01"
    assert clock.now >= 3


def test_stability_timer_restarts_when_result_changes(clock):
    header = "CPF: 12345678901"
    complete = header + "\nNASCIMENTO: 01/01/1990"
    tab = ResultTab(clock, lambda now: header if now < 0.75 else complete)
    assert mind7._aguardar_resultado_mind7(tab, 4) == complete
    assert clock.now >= 1.75


def test_completed_record_without_birth_date_does_not_need_timeout(clock):
    text = "CPF: 12345678901\nNOME: PESSOA TESTE"
    tab = ResultTab(clock, lambda _: text)
    assert mind7._aguardar_resultado_mind7(tab, 25) == text
    assert clock.now == 1


def test_network_error_reenabling_button_is_not_no_data(clock):
    tab = ResultTab(clock, lambda _: "", busy_until=1, message="")
    with pytest.raises(TimeoutError):
        mind7._aguardar_resultado_mind7(tab, 3)


def test_foreign_login_page_never_receives_credentials():
    tab = Mock(url="https://example.invalid/acesso/?next=mind-7.org/painel/consultas/cpf/")
    assert mind7._garantir_login_mind7(tab) is False
    tab.ele.assert_not_called()
