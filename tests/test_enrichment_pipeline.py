from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock
import io

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from src.automation import mind7_scraper as mind7
from src.automation import cloudflare_solver as cloudflare
from src.database import connection
from src.database.models import Campaign, Contact
from src.database.crud import update_contact
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
    assert lookup.call_args.args[6] == {"entry_value": "", "uf": "PR", "has_cnh": True}


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
