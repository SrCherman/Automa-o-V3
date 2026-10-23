"""Todos os envios usam mocks; nunca conectar aos portais ou navegadores reais."""
import importlib
import builtins
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from credit_pipeline import processar as legacy
from src.automation import mind7_rate_limit as quota
from src.automation import mind7_scraper as shared


@pytest.fixture(autouse=True)
def isolated_mind7_state(tmp_path, monkeypatch):
    """Nunca usar o marcador ou ledger de produção nos testes."""
    monkeypatch.setattr(quota, "BLOCK_FILE", tmp_path / "mind7_access_blocked.txt")
    monkeypatch.setattr(quota, "LEDGER_FILE", tmp_path / "mind7_query_quota.sqlite")


@pytest.fixture
def app_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "credit_pipeline"))
    for key in ("MIND7_URL", "MIND7_USERNAME", "MIND7_PASSWORD", "ITAU_URL", "ITAU_USERNAME", "ITAU_PASSWORD"):
        monkeypatch.setenv(key, "offline-test")
    return (importlib.import_module("app.services.scraper"),
            importlib.import_module("app.services.api_client"))


@pytest.mark.parametrize("path", ["phone", "cpf", "legacy", "app", "http"])
def test_persistent_block_prevents_every_submission_path(app_modules, path):
    browser, api = app_modules
    quota.BLOCK_FILE.write_text("Registro original", encoding="utf-8")
    page = Mock()
    client = api.DirectAPIClient()
    client.session = Mock()
    calls = {
        "phone": lambda: shared.consultar_mind7_celular(page, "45999990000", "Pessoa"),
        "cpf": lambda: shared.consultar_mind7_cpf(page, "12345678901"),
        "legacy": lambda: legacy.consultar_mind7(page, "45999990000", "Pessoa"),
        "app": lambda: browser.consultar_mind7(page, "45999990000", "Pessoa"),
        "http": lambda: client.consultar_mind7_api("45999990000", "", "Pessoa"),
    }
    with pytest.raises(quota.Mind7AccessBlocked):
        calls[path]()
    assert page.mock_calls == []
    client.session.post.assert_not_called()
    assert quota.BLOCK_FILE.read_text(encoding="utf-8") == "Registro original"


@pytest.mark.parametrize("path", ["phone", "cpf", "legacy", "app"])
def test_browser_query_submits_once_without_enter(app_modules, monkeypatch, path):
    browser, _ = app_modules
    field, button = Mock(), Mock()
    tab = Mock(url="https://mind-7.org/painel/consultas/" + ("cpf/" if path == "cpf" else "celular/"))
    tab.html = "Não foi possível encontrar"
    def element(selector, **kwargs):
        if selector == "#documento":
            return field
        if selector == "#botao-consultar":
            return button
        if selector == "tag:body":
            return SimpleNamespace(raw_text="")
    tab.ele.side_effect = element
    monkeypatch.setattr(shared, "_garantir_login_mind7", lambda tab: True)
    waiting = Mock(return_value=None)
    monkeypatch.setattr(shared, "_aguardar_resultado_mind7", waiting)
    monkeypatch.setattr(shared.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(legacy, "pegar_aba", lambda *args: tab)
    monkeypatch.setattr(browser, "obter_aba_existente", lambda *args: tab)
    document = "12345678901" if path == "cpf" else "45999990000"
    if path == "phone":
        shared.consultar_mind7_celular(Mock(), document, "Pessoa", tab=tab)
    elif path == "cpf":
        shared.consultar_mind7_cpf(Mock(), document, tab=tab)
    elif path == "legacy":
        legacy.consultar_mind7(Mock(), document, "Pessoa")
    else:
        browser.consultar_mind7(Mock(), document, "Pessoa")
    field.input.assert_called_once_with(document, clear=True)
    button.click.assert_called_once_with()
    waiting.assert_called_once_with(tab, {"phone": 30, "cpf": 25, "legacy": 180, "app": 25}[path],
                                    listener=tab.listen, stop_event=None)
    tab.listen.start.assert_called_once_with(targets=True, method="POST")
    tab.listen.stop.assert_called_once_with()
    tab.refresh.assert_not_called()


def test_browser_guard_contains_send_and_full_result_wait(monkeypatch):
    tab, button = Mock(), Mock()
    result = "CPF: 12345678901\nNOME: PESSOA TESTE"
    events = []
    button.click.side_effect = lambda: events.append("click")
    tab.scroll.to_bottom.side_effect = lambda: events.append("scroll")
    def wait(*args, **kwargs):
        assert events == ["guard_start", "click", "scroll"]
        assert kwargs["listener"] is tab.listen
        assert kwargs["stop_event"] is None
        events.append("loaded")
        return result
    def guard(*, stop_event, send):
        events.append("guard_start")
        response = send()
        assert events[-1] == "loaded"
        events.append("guard_finished")
        return response
    monkeypatch.setattr(shared, "reserve_mind7_request", guard)
    monkeypatch.setattr(shared, "_verificar_bloqueio_mind7", lambda tab: None)
    monkeypatch.setattr(shared, "_aguardar_resultado_mind7", wait)
    assert shared.enviar_consulta_mind7(tab, button) == result
    assert events == ["guard_start", "click", "scroll", "loaded", "guard_finished"]


@pytest.mark.parametrize("error,refresh", [
    (shared.Mind7GhostClick("nenhum sinal"), True),
    (TimeoutError("resposta lenta/incerta"), False),
])
def test_ghost_click_refreshes_page_but_never_resubmits(monkeypatch, error, refresh):
    tab, button = Mock(), Mock()
    monkeypatch.setattr(shared, "reserve_mind7_request", lambda **kwargs: kwargs["send"]())
    monkeypatch.setattr(shared, "_verificar_bloqueio_mind7", lambda _: None)
    monkeypatch.setattr(shared, "_aguardar_resultado_mind7", Mock(side_effect=error))
    with pytest.raises(shared.Mind7Unavailable if refresh else TimeoutError):
        shared.enviar_consulta_mind7(tab, button)
    button.click.assert_called_once_with()
    if refresh:
        tab.refresh.assert_called_once_with()
    else:
        tab.refresh.assert_not_called()
    tab.listen.stop.assert_called_once_with()


@pytest.mark.parametrize("status,text", [(403, "Forbidden"), (429, "Too many requests"),
                                         (200, "<div>Bloqueio automático</div>"),
                                         (200, "<title>Just a moment...</title><p>Enable JavaScript and cookies to continue</p>")])
def test_http_block_response_persists_and_prevents_next_send(app_modules, status, text):
    _, api = app_modules
    client = api.DirectAPIClient()
    client.session = Mock()
    client.session.post.return_value = SimpleNamespace(status_code=status, text=text)
    for _ in range(2):
        with pytest.raises(quota.Mind7AccessBlocked):
            client.consultar_mind7_api("45999990000", "", "Pessoa")
    client.session.post.assert_called_once()
    options = client.session.post.call_args.kwargs
    assert options["timeout"] == 30 and options["allow_redirects"] is False
    assert options["headers"]["User-Agent"].startswith("Mozilla/5.0")
    assert "Edg/" in options["headers"]["User-Agent"]
    assert quota.BLOCK_FILE.exists()


def test_http_empty_result_is_a_single_request(app_modules):
    _, api = app_modules
    client = api.DirectAPIClient()
    client.session = Mock()
    client.session.post.return_value = SimpleNamespace(status_code=200, text="Nenhum registro encontrado")
    assert client.consultar_mind7_api("45999990000", "", "Pessoa") is None
    client.session.post.assert_called_once()


def test_http_transport_timeout_is_pending_without_retry(app_modules):
    import requests
    _, api = app_modules
    client = api.DirectAPIClient()
    client.session = Mock()
    client.session.post.side_effect = requests.Timeout("Resposta perdida")
    with pytest.raises(shared.Mind7Unavailable):
        client.consultar_mind7_api("45999990000", "", "Pessoa")
    client.session.post.assert_called_once()


@pytest.fixture
def legacy_app(app_modules, monkeypatch, tmp_path):
    original_open = builtins.open
    def test_log(file, *args, **kwargs):
        if str(file).endswith("processamento.log"):
            file = tmp_path / "processamento.log"
        return original_open(file, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", test_log)
    stdout, stderr = sys.stdout, sys.stderr
    try:
        main = importlib.import_module("app.main")
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    monkeypatch.setattr(main, "BLOCK_FILE", quota.BLOCK_FILE)
    return main


@pytest.mark.parametrize("outcome", [None, TimeoutError("Sem resposta"), quota.Mind7AccessBlocked("Bloqueado")])
def test_api_result_or_failure_never_falls_back_to_browser(legacy_app, monkeypatch, outcome):
    main = legacy_app
    monkeypatch.setenv("PROCESS_MAX_WORKERS", "1")
    monkeypatch.setattr(main, "listar_pendentes", lambda: [dict(row_num=2, nome_whatsapp="Pessoa", telefone="45999990000")])
    monkeypatch.setattr(main, "marcar_processando", Mock())
    result = Mock()
    monkeypatch.setattr(main, "atualizar_resultado", result)
    client = Mock()
    client._obter_auth_do_navegador.return_value = {"mind7_cookies": "offline"}
    if isinstance(outcome, Exception):
        client.consultar_mind7_api.side_effect = outcome
    else:
        client.consultar_mind7_api.return_value = outcome
    monkeypatch.setattr(main, "api_client", client)
    monkeypatch.setattr(main, "iniciar_navegador", Mock())
    visual = Mock()
    monkeypatch.setattr(main, "consultar_mind7", visual)
    main._processar_pendentes()
    visual.assert_not_called()
    client.consultar_mind7_api.assert_called_once()
    assert result.call_args.args[3] == ("PENDENTE" if isinstance(outcome, Exception) else "SEM DADOS")
    assert main.processando is False


def test_blocked_legacy_endpoints_and_worker_do_not_start(legacy_app, monkeypatch):
    from fastapi import BackgroundTasks
    main = legacy_app
    quota.BLOCK_FILE.write_text("Bloqueio original", encoding="utf-8")
    browser = Mock()
    monkeypatch.setattr(main, "iniciar_navegador", browser)
    pending = Mock(return_value=[])
    monkeypatch.setattr(main, "listar_pendentes", pending)
    main._processar_pendentes()
    pending.assert_not_called()
    browser.assert_not_called()
    tasks = BackgroundTasks()
    # Estes handlers retornam sem await; não criar sockets do event loop Windows.
    def finish(coroutine):
        with pytest.raises(StopIteration) as result:
            coroutine.send(None)
        return result.value.value
    assert finish(main.iniciar_processamento(tasks))["status"] == "blocked"
    assert finish(main.automation_start())["status"] == "blocked"
    assert finish(main.automation_state())["mind7_access_blocked"] is True
    assert tasks.tasks == []
