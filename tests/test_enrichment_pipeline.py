from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock
import io
import threading

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from src.automation import mind7_scraper as mind7
from src.automation import cloudflare_solver as cloudflare
from src.database import connection
from src.database.models import Campaign, Contact
from src.database.crud import get_dashboard_stats, update_contact
from src.services import orchestrator as flow
from src.services.export_service import export_contacts_to_excel
from openpyxl import load_workbook


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite'}", connect_args={"check_same_thread": False})
    connection.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(flow, "SessionLocal", factory)
    with factory() as db:
        db.add(Campaign(id=1, name="Teste"))
        db.commit()
    yield factory
    engine.dispose()


def contact(db, **kwargs):
    values = dict(campaign_id=1, raw_phone="45999990000", phone="45999990000", cpf="12345678901",
                  clean_name="Pessoa de Teste", credline_status="REPROVADO")
    values.update(kwargs)
    c = Contact(**values)
    db.add(c)
    db.commit()
    return c.id


def worker():
    w = flow.CampaignWorkerSession(1, "Teste", "ABC1D23", "130.000,00")
    w.is_armed = True
    return w


def test_extraction_preserves_birth_date_and_cpf():
    result = mind7.extrair_dados_cpf("CPF: 123.456.789-01\nNASCIMENTO: 31/12/1990\nReceita Federal: Rua Brasil, 120 CEP: 85800-000", "12345678901")
    assert result["birth_date"] == "1990-12-31"
    assert "age" not in result
    assert (result["cep"], result["house_number"]) == ("85800000", "120")


@pytest.mark.parametrize("value", [date(1990, 1, 1), datetime(1990, 1, 1), "01/01/1990", "1990-01-01"])
def test_birth_date_from_excel_or_portal(value):
    assert mind7.normalizar_nascimento(value) == "1990-01-01"


@pytest.mark.parametrize("text", ["CPF: 98765432100\nNASCIMENTO: 01/01/1990", "NASCIMENTO: 01/01/1990", "CPF: 12345678901\nCPF: 98765432100"])
def test_extraction_rejects_other_identity(text):
    with pytest.raises(ValueError):
        mind7.extrair_dados_cpf(text, "12345678901")


def test_bigdata_relatives_do_not_replace_or_invalidate_subject():
    text = "--- DADOS BÁSICOS COMPLETO: ---\nCPF: 12345678901\nNASCIMENTO: 03/05/1964\n--- ENDERECOS ---\nRua Brasil, 20 CEP: 85800-000\n--- PARENTES: ---\nCPF: 98765432100\nNASCIMENTO: 01/01/1990"
    result = mind7.extrair_dados_cpf(text, "12345678901")
    assert result["birth_date"] == "1964-05-03"
    assert result["cep"] == "85800000"


def test_missing_subject_birth_date_never_uses_relative_birth_date():
    text = "CPF: 12345678901\n--- PARENTES: ---\nCPF: 98765432100\nNASCIMENTO: 01/01/1990"
    assert not mind7.extrair_dados_cpf(text, "12345678901")["birth_date"]
    with pytest.raises(ValueError):
        mind7.extrair_dados_cpf(text, "98765432100")


@pytest.mark.parametrize("details", ["IDADE: 30 anos", "Atualização: 01/01/1990", "NASCIMENTO: 31/02/1990"])
def test_never_invents_birthday_from_age_or_other_date(details):
    assert not mind7.extrair_dados_cpf("CPF: 12345678901\n" + details, "12345678901")["birth_date"]


def test_cloudflare_does_not_report_success_on_timeout(monkeypatch):
    tab = SimpleNamespace(title="Just a moment", html="executando verificação")
    ticks = iter([0, 0, 13])
    monkeypatch.setattr(cloudflare.time, "time", lambda: next(ticks))
    monkeypatch.setattr(cloudflare.time, "sleep", lambda _: None)
    assert cloudflare.check_and_solve_cloudflare(tab) is False


def test_enrichment_required_for_rejected_and_idempotent(database, monkeypatch):
    with database() as db:
        cid = contact(db)
    lookup = Mock(return_value={"birth_date": "1990-01-01", "cep": "85800000", "house_number": "20", "age": "36 anos"})
    monkeypatch.setattr(flow, "consultar_mind7_cpf", lookup)
    w = worker()
    w._enriquecer_contato_single(cid, Mock(), tab="cpf-tab")
    w._enriquecer_contato_single(cid, Mock(), tab="cpf-tab")
    with database() as db:
        c = db.get(Contact, cid)
        assert c.credline_status == "REPROVADO"
        assert c.enrichment_status == "CONCLUIDO"
        assert c.birth_date == "1990-01-01"
        assert w._next_contact(db, "C6").id == cid
    lookup.assert_called_once_with(lookup.call_args.args[0], "12345678901", tab="cpf-tab")


def test_same_cpf_reuses_completed_lookup(database, monkeypatch):
    with database() as db:
        contact(db, enrichment_status="CONCLUIDO", birth_date="1990-01-01")
        cid = contact(db, phone="45999990001")
    lookup = Mock()
    monkeypatch.setattr(flow, "consultar_mind7_cpf", lookup)
    worker()._enriquecer_contato_single(cid, Mock())
    lookup.assert_not_called()
    with database() as db:
        assert db.get(Contact, cid).birth_date == "1990-01-01"


@pytest.mark.parametrize("cached_status", ["INCOMPLETO", "SEM DADOS"])
def test_incomplete_cpf_cache_does_not_block_new_lookup(database, monkeypatch, cached_status):
    with database() as db:
        contact(db, enrichment_status=cached_status)
        cid = contact(db, phone="45999990001")
    lookup = Mock(return_value={"birth_date": "1990-01-01"})
    monkeypatch.setattr(flow, "consultar_mind7_cpf", lookup)
    worker()._enriquecer_contato_single(cid, Mock())
    lookup.assert_called_once()
    with database() as db:
        assert db.get(Contact, cid).birth_date == "1990-01-01"


def test_missing_birth_date_is_final_for_enrichment_but_waits_c6(database, monkeypatch):
    with database() as db:
        cid = contact(db, credline_status="CPF COLETADO")
    monkeypatch.setattr(flow, "consultar_mind7_cpf", lambda *a, **kw: {"cep": "85800000"})
    w = worker()
    w._enriquecer_contato_single(cid, Mock())
    w._simular_c6_single(cid, Mock())
    with database() as db:
        assert w._next_contact(db, "ENRIQUECIMENTO") is None
        assert w._next_contact(db, "CREDLINE").id == cid
        assert db.get(Contact, cid).c6_status == "AGUARDANDO DADOS"
        update_contact(db, cid, {"birth_date": "1990-01-01"})
        assert w._next_contact(db, "C6").id == cid


def test_enrichment_transient_errors_are_bounded(database, monkeypatch):
    with database() as db:
        cid = contact(db)
    lookup = Mock(side_effect=TimeoutError("timeout"))
    monkeypatch.setattr(flow, "consultar_mind7_cpf", lookup)
    w = worker()
    for _ in range(3):
        w._enriquecer_contato_single(cid, Mock())
    with database() as db:
        assert db.get(Contact, cid).enrichment_attempts == 3
        assert w._next_contact(db, "ENRIQUECIMENTO") is None


def test_challenge_preserves_pending_instead_of_no_data(database, monkeypatch):
    with database() as db:
        cid = contact(db)
    monkeypatch.setattr(flow, "consultar_mind7_cpf", Mock(side_effect=mind7.Mind7Unavailable("login")))
    with pytest.raises(mind7.Mind7Unavailable):
        worker()._enriquecer_contato_single(cid, Mock())
    with database() as db:
        c = db.get(Contact, cid)
        assert c.enrichment_status == "PENDENTE"
        assert c.enrichment_attempts == 0


def test_cpf_lookup_recovers_when_mind7_opens_dashboard(monkeypatch):
    campo = Mock()
    botao = Mock()
    class Tab:
        url = "https://mind-7.org/painel/"
        title = "MIND7 - Painel"
        html = ""
        navigations = 0

        def get(self, url):
            self.url = url if self.navigations else "https://mind-7.org/painel/"
            self.navigations += 1

        def ele(self, selector, timeout=0):
            if selector == "#documento":
                return campo if self.navigations == 2 else None
            if selector == "#botao-consultar":
                return botao
            if selector == "#copiar_dados":
                return SimpleNamespace(value="CPF: 12345678901\nNASCIMENTO: 01/01/1990")

        def run_js(self, script):
            pass

    tab = Tab()
    monkeypatch.setattr(mind7, "check_and_solve_cloudflare", lambda _: True)
    monkeypatch.setattr(mind7, "_garantir_login_mind7", lambda _: True)
    monkeypatch.setattr(mind7.time, "sleep", lambda _: None)
    result = mind7.consultar_mind7_cpf(Mock(), "12345678901", tab=tab)
    assert tab.navigations == 2
    assert result["birth_date"] == "1990-01-01"
    botao.click.assert_called_once()


def test_c6_uncertain_submission_never_retries(database, monkeypatch):
    with database() as db:
        cid = contact(db, birth_date="1990-01-01", enrichment_status="CONCLUIDO")
    def simulate(*args):
        args[-1]()
        raise TimeoutError("resposta perdida")
    lookup = Mock(side_effect=simulate)
    monkeypatch.setattr(flow, "consultar_c6_cpf", lookup)
    w = worker()
    w._simular_c6_single(cid, Mock())
    w._recover()
    w._simular_c6_single(cid, Mock())
    with database() as db:
        assert db.get(Contact, cid).c6_status == "CONFERIR RESULTADO"
        assert w._next_contact(db, "C6") is None
    assert lookup.call_count == 1


def test_c6_result_independent_from_itau_and_persists(database, monkeypatch):
    with database() as db:
        cid = contact(db, birth_date="1990-01-01", enrichment_status="CONCLUIDO")
    lookup = Mock(return_value={"status": "APROVADO", "financed_value": "R$ 117.000,00"})
    monkeypatch.setattr(flow, "consultar_c6_cpf", lookup)
    w = worker()
    w._simular_c6_single(cid, Mock())
    w._simular_c6_single(cid, Mock())
    with database() as db:
        c = db.get(Contact, cid)
        assert c.c6_financed_value == "R$ 117.000,00"
        assert c.credline_status == "REPROVADO"
    assert lookup.call_count == 1
    assert lookup.call_args.args[4:6] == ("SEO7H39", "130.000,00")
    assert lookup.call_args.args[6] == {"entry_value": "", "uf": "PR", "has_cnh": True,
                                        "vehicle_type": "VEÍCULOS LEVES"}


def test_cpf_change_clears_results_from_previous_identity(database):
    with database() as db:
        cid = contact(db, birth_date="1990-01-01", enrichment_status="CONCLUIDO", c6_status="APROVADO", c6_financed_value="R$ 117.000,00")
        c = update_contact(db, cid, {"cpf": "98765432100", "birth_date": "1990-01-01", "credline_status": "REPROVADO"})
        assert c.birth_date is None and c.c6_financed_value is None
        assert c.enrichment_status == c.c6_status == "PENDENTE"
        assert c.credline_status == "CPF COLETADO"


def test_schema_migration_is_additive_and_repeatable(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE contacts (id INTEGER PRIMARY KEY, cpf VARCHAR(14), credline_status VARCHAR(50))"))
        conn.execute(text("INSERT INTO contacts VALUES (1, '12345678901', 'REPROVADO')"))
    monkeypatch.setattr(connection, "engine", engine)
    connection.init_db()
    connection.init_db()
    assert "birth_date" in {c["name"] for c in inspect(engine).get_columns("contacts")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT cpf, credline_status, enrichment_status FROM contacts")).one() == ("12345678901", "REPROVADO", "PENDENTE")
    assert path.with_suffix(".before_c6.sqlite").exists()
    engine.dispose()


def test_parallel_stages_keep_fixed_tabs_and_process_new_cpf(database, monkeypatch):
    with database() as db:
        cid = contact(db, cpf=None, credline_status="PENDENTE")
    tabs = [SimpleNamespace(url=flow.MIND7_URL), SimpleNamespace(url=flow.MIND7_CPF_URL)]
    page = Mock()
    page.get_tabs.return_value = tabs
    monkeypatch.setattr(flow, "get_campaign_browser_page", lambda *args: page)
    received = []
    def phone(*args, tab=None):
        received.append(("phone", tab))
        return {"cpf": "12345678901", "nome": "Pessoa de Teste"}
    def details(*args, tab=None):
        received.append(("cpf", tab))
        return {"birth_date": "1990-01-01"}
    monkeypatch.setattr(flow, "consultar_mind7_celular", phone)
    monkeypatch.setattr(flow, "consultar_mind7_cpf", details)
    monkeypatch.setitem(flow.CampaignWorkerSession.STAGES, "AUTO", ("MIND7", "ENRIQUECIMENTO"))
    w = worker()
    w.start()
    w._thread.join(timeout=8)
    assert not w._thread.is_alive()
    assert received == [("phone", tabs[0]), ("cpf", tabs[1])]
    with database() as db:
        assert db.get(Contact, cid).birth_date == "1990-01-01"
    page.new_tab.assert_not_called()


def test_export_replaces_age_and_keeps_c6_separate(database):
    with database() as db:
        contact(db, age="36 anos", birth_date="1990-01-01", c6_status="APROVADO", c6_financed_value="R$ 117.000,00")
        wb = load_workbook(io.BytesIO(export_contacts_to_excel(db)))
    rows = list(wb.active.values)
    headers = rows[0]
    assert "Idade" not in headers
    assert headers.count("Data de Nascimento") == 1
    assert rows[1][headers.index("Data de Nascimento")] == "1990-01-01"
    assert rows[1][headers.index("Status Itaú")] == "REPROVADO"
    assert rows[1][headers.index("Valor Financiado C6")] == "R$ 117.000,00"


def test_manual_retry_requires_explicit_request(database):
    with database() as db:
        cid = contact(db, c6_status="CONFERIR RESULTADO", c6_attempts=3)
        assert update_contact(db, cid, {"clean_name": "Nome corrigido"}).c6_status == "CONFERIR RESULTADO"
        c = update_contact(db, cid, {"retry_c6": True})
        assert (c.c6_status, c.c6_attempts) == ("PENDENTE", 0)


def test_c6_ready_contact_does_not_open_mind7(database, monkeypatch):
    with database() as db:
        cid = contact(db, birth_date="1990-01-01", enrichment_status="CONCLUIDO")
    opened = []
    page = Mock()
    page.get_tabs.return_value = []
    monkeypatch.setattr(flow, "get_campaign_browser_page", lambda campaign, service: opened.append(service) or page)
    def simulate(*args):
        args[-1]()
        return {"status": "APROVADO", "financed_value": "R$ 117.000,00"}
    monkeypatch.setattr(flow, "consultar_c6_cpf", simulate)
    w = flow.CampaignWorkerSession(1, "Teste", "ABC1D23", "130.000,00", "C6")
    w._stop.wait = Mock()
    w.start()
    w._thread.join(timeout=5)
    assert not w._thread.is_alive()
    assert opened == ["c6"]
    page.new_tab.assert_called_once_with(flow.C6_URL)
    with database() as db:
        assert db.get(Contact, cid).c6_status == "APROVADO"


@pytest.mark.parametrize("stage,service,url", [
    ("C6", "c6", "https://c6auto.com.br/originacaolojista/shopkeeper-panel"),
    ("CREDLINE", "itau", "https://www.credlineitau.com.br/new-simulator"),
])
def test_bank_stage_opens_browser_before_a_contact_is_ready(database, monkeypatch, stage, service, url):
    with database() as db:
        cid = contact(db, enrichment_status="INCOMPLETO")
    opened = []
    page = Mock()
    page.get_tabs.return_value = []
    monkeypatch.setattr(flow, "get_campaign_browser_page", lambda campaign, service: opened.append(service) or page)
    w = flow.CampaignWorkerSession(1, "Teste", "ABC1D23", "130.000,00", stage)
    w._stop.wait = Mock()
    w.start()
    w._thread.join(timeout=5)
    assert not w._thread.is_alive()
    assert opened == [service]
    page.new_tab.assert_called_once_with(url)
    with database() as db:
        expected = "AGUARDANDO DADOS" if stage == "C6" else "PENDENTE"
        assert db.get(Contact, cid).c6_status == expected


@pytest.mark.parametrize("stage,url", [
    ("MIND7", flow.MIND7_URL),
    ("ENRIQUECIMENTO", flow.MIND7_CPF_URL),
])
def test_explicit_mind7_stage_opens_even_with_no_eligible_contacts(database, monkeypatch, stage, url):
    opened = []
    page = Mock()
    page.get_tabs.return_value = []
    monkeypatch.setattr(flow, "get_campaign_browser_page", lambda campaign, service: opened.append(service) or page)
    w = flow.CampaignWorkerSession(1, "Teste", "ABC1D23", "130.000,00", stage)
    w.start()
    w._thread.join(timeout=5)
    assert not w._thread.is_alive()
    assert opened == ["mind7"]
    page.new_tab.assert_called_once_with(url)


def test_ready_c6_does_not_wait_for_mind7_browser(database, monkeypatch):
    with database() as db:
        contact(db, birth_date="1990-01-01", enrichment_status="CONCLUIDO")
        contact(db, phone="45999990001")
    release_mind7 = threading.Event()
    c6_started = threading.Event()
    def browser(campaign, service):
        if service == "mind7":
            release_mind7.wait(5)
            raise mind7.Mind7Unavailable("Login Mind7 pendente")
        c6_started.set()
        page = Mock()
        page.get_tabs.return_value = []
        return page
    monkeypatch.setattr(flow, "get_campaign_browser_page", browser)
    monkeypatch.setattr(flow, "consultar_c6_cpf", Mock(side_effect=flow.C6Unavailable("Login C6 pendente")))
    w = flow.CampaignWorkerSession(1, "Teste", "ABC1D23", "130.000,00", "C6")
    w.start()
    try:
        assert c6_started.wait(1), "O navegador Mind7 bloqueou o worker C6"
    finally:
        release_mind7.set()
        w._thread.join(timeout=5)
    assert not w._thread.is_alive()


def test_c6_login_pause_does_not_exhaust_attempts(database, monkeypatch):
    with database() as db:
        cid = contact(db, birth_date="1990-01-01", enrichment_status="CONCLUIDO")
    monkeypatch.setattr(flow, "consultar_c6_cpf", Mock(side_effect=flow.C6Unavailable("Login C6 pendente")))
    w = worker()
    for _ in range(4):
        with pytest.raises(flow.C6Unavailable):
            w._simular_c6_single(cid, Mock())
    with database() as db:
        assert db.get(Contact, cid).c6_attempts == 0
        assert w._next_contact(db, "C6").id == cid


def test_restart_restores_pre_submit_c6_attempt(database):
    with database() as db:
        cid = contact(db, birth_date="1990-01-01", enrichment_status="CONCLUIDO",
                      c6_status="PROCESSANDO", c6_attempts=3)
    w = worker()
    w._recover()
    with database() as db:
        c = db.get(Contact, cid)
        assert (c.c6_status, c.c6_attempts) == ("PENDENTE", 2)
        assert w._next_contact(db, "C6").id == cid


def test_itau_response_cannot_attach_to_changed_identity(database, monkeypatch):
    with database() as db:
        cid = contact(db, enrichment_status="CONCLUIDO", birth_date="1990-01-01",
                      credline_status="CPF COLETADO")
    def simulate(*args):
        with database() as db:
            db.query(Contact).filter(Contact.id == cid).update(
                {Contact.cpf: "98765432100", Contact.credline_status: "CPF COLETADO"},
                synchronize_session=False)
            db.commit()
        return {"status": "CORRENTISTA", "financed_value": "R$ 50.000,00"}
    monkeypatch.setattr(flow, "consultar_credline_cpf", simulate)
    worker()._simular_itau_single(cid, Mock())
    with database() as db:
        c = db.get(Contact, cid)
        assert (c.cpf, c.credline_status, c.financed_value) == ("98765432100", "CPF COLETADO", None)


def test_worker_cannot_process_contact_moved_to_other_campaign(database, monkeypatch):
    with database() as db:
        db.add(Campaign(id=2, name="Other"))
        cid = contact(db, enrichment_status="CONCLUIDO", birth_date="1990-01-01")
        db.query(Contact).filter(Contact.id == cid).update({Contact.campaign_id: 2}, synchronize_session=False)
        db.commit()
    lookup = Mock()
    monkeypatch.setattr(flow, "consultar_c6_cpf", lookup)
    worker()._simular_c6_single(cid, Mock())
    lookup.assert_not_called()
    with database() as db:
        assert db.get(Contact, cid).c6_status == "PENDENTE"


def test_paused_mind7_reason_is_available_after_worker_stops(database, monkeypatch):
    with database() as db:
        contact(db)
    page = Mock()
    page.get_tabs.return_value = [SimpleNamespace(url=flow.MIND7_CPF_URL)]
    monkeypatch.setattr(flow, "get_campaign_browser_page", lambda *args: page)
    monkeypatch.setattr(flow, "consultar_mind7_cpf", Mock(side_effect=mind7.Mind7Unavailable("Abra o formulário CPF")))
    w = flow.CampaignWorkerSession(1, "Teste", "ABC1D23", "130.000,00", "ENRIQUECIMENTO")
    w.start()
    w._thread.join(timeout=5)
    assert not w._thread.is_alive()
    manager = flow.MultiCampaignOrchestrator()
    manager.active_sessions[1] = w
    state = manager.get_state(1)
    assert state["is_running"] is False
    assert state["paused_campaigns"][0]["stage_errors"]["ENRIQUECIMENTO"] == "Abra o formulário CPF"


def test_dashboard_reports_birth_dates_and_c6_outcomes_separately(database):
    with database() as db:
        contact(db, birth_date="1990-01-01", enrichment_status="CONCLUIDO",
                c6_status="APROVADO", phone="45999990000")
        contact(db, c6_status="CONFERIR RESULTADO", phone="45999990001")
        stats = get_dashboard_stats(db, 1)
    assert stats["cpfs_coletados"] == 2
    assert (stats["birth_dates"], stats["c6_finished"], stats["c6_review"]) == (1, 1, 1)
