from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.routes_contacts import ContactUpdate, edit_contact
from src.database.connection import Base
from src.database.crud import ContactQueryInProgress, update_contact
from src.database.models import Campaign, Contact
from src.services import orchestrator as flow


@pytest.fixture
def query_case(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'identity.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(flow, "SessionLocal", factory)
    with factory() as db:
        db.add(Campaign(id=1, name="Teste de identidade"))
        db.flush()
        contact = Contact(campaign_id=1, raw_phone="45999990000", phone="45999990000",
                          cpf="12345678901", clean_name="Pessoa de teste", birth_date="1990-01-01",
                          enrichment_status="CONCLUIDO", c6_status="PENDENTE")
        db.add(contact)
        db.commit()
        contact_id = contact.id
    worker = flow.CampaignWorkerSession(1, "Teste", "ABC1D23", "130.000,00", "C6")
    worker.is_armed = True
    yield factory, contact_id, worker
    engine.dispose()


def change(factory, contact_id, **values):
    with factory() as db:
        db.query(Contact).filter(Contact.id == contact_id).update(values, synchronize_session=False)
        db.commit()


@pytest.mark.parametrize("fails", [False, True])
def test_enrichment_discards_return_for_previous_cpf(query_case, monkeypatch, fails):
    factory, contact_id, worker = query_case
    change(factory, contact_id, enrichment_status="PENDENTE", birth_date=None)

    def lookup(page, cpf, tab=None):
        assert cpf == "12345678901"
        change(factory, contact_id, cpf="98765432100", enrichment_status="PENDENTE", enrichment_attempts=0)
        if fails:
            raise TimeoutError("Retorno antigo")
        return {"birth_date": "1990-01-01", "cep": "85800000"}

    monkeypatch.setattr(flow, "consultar_mind7_cpf", lookup)
    worker._enriquecer_contato_single(contact_id, Mock())
    with factory() as db:
        contact = db.get(Contact, contact_id)
        assert contact.cpf == "98765432100"
        assert contact.birth_date is contact.cep is None
        assert contact.enrichment_status == "PENDENTE"
        assert contact.enrichment_attempts == 0


@pytest.mark.parametrize("old_cep", [None, "11111111"])
@pytest.mark.parametrize("returned_address", [False, True])
def test_enrichment_preserves_address_edited_during_lookup(query_case, monkeypatch, old_cep, returned_address):
    factory, contact_id, worker = query_case
    change(factory, contact_id, enrichment_status="PENDENTE", birth_date=None, cep=old_cep, house_number="11")

    def lookup(*args, **kwargs):
        with factory() as db:
            update_contact(db, contact_id, {"cep": "22222222", "house_number": "22"})
        result = {"birth_date": "1990-01-01"}
        if returned_address:
            result.update(cep="33333333", house_number="33")
        return result

    monkeypatch.setattr(flow, "consultar_mind7_cpf", lookup)
    worker._enriquecer_contato_single(contact_id, Mock())
    with factory() as db:
        contact = db.get(Contact, contact_id)
        assert (contact.cep, contact.house_number) == ("22222222", "22")
        assert contact.birth_date == "1990-01-01" and contact.enrichment_status == "CONCLUIDO"


@pytest.mark.parametrize("field,value", [("cpf", "98765432100"), ("phone", "45999990001"), ("birth_date", "1991-01-01")])
@pytest.mark.parametrize("fails", [False, True])
def test_c6_discards_old_identity_return_after_submission(query_case, monkeypatch, field, value, fails):
    factory, contact_id, worker = query_case

    def lookup(*args):
        assert args[1:4] == ("12345678901", "45999990000", "1990-01-01")
        args[-1]()
        change(factory, contact_id, **{field: value}, c6_status="CONFERIR RESULTADO")
        if fails:
            raise TimeoutError("Retorno antigo")
        return {"status": "APROVADO", "financed_value": "R$ 77.000,00"}

    monkeypatch.setattr(flow, "consultar_c6_cpf", lookup)
    worker._simular_c6_single(contact_id, Mock())
    with factory() as db:
        contact = db.get(Contact, contact_id)
        assert getattr(contact, field) == value
        assert contact.c6_status == "CONFERIR RESULTADO"
        assert contact.c6_financed_value is None


@pytest.mark.parametrize("field,value", [("cpf", "98765432100"), ("phone", "45999990001"), ("birth_date", "1991-01-01")])
def test_c6_does_not_submit_after_identity_changes(query_case, monkeypatch, field, value):
    factory, contact_id, worker = query_case
    click = Mock()

    def lookup(*args):
        change(factory, contact_id, **{field: value}, c6_status="PENDENTE")
        args[-1]()
        click()

    monkeypatch.setattr(flow, "consultar_c6_cpf", lookup)
    worker._simular_c6_single(contact_id, Mock())
    click.assert_not_called()
    with factory() as db:
        assert db.get(Contact, contact_id).c6_status == "PENDENTE"


@pytest.mark.parametrize("stage", ["enrichment", "c6"])
@pytest.mark.parametrize("fails", [False, True])
def test_old_return_preserves_newer_attempt_or_completed_result(query_case, monkeypatch, stage, fails):
    factory, contact_id, worker = query_case
    if stage == "enrichment":
        change(factory, contact_id, enrichment_status="PENDENTE", birth_date=None)

    def lookup(*args, **kwargs):
        if stage == "c6":
            args[-1]()
            change(factory, contact_id, c6_status="APROVADO", c6_financed_value="R$ 25.000,00", c6_attempts=2)
        else:
            change(factory, contact_id, enrichment_status="PROCESSANDO", enrichment_attempts=2)
        if fails:
            raise TimeoutError("Retorno antigo")
        return {"birth_date": "1992-01-01", "status": "APROVADO", "financed_value": "R$ 77.000,00"}

    monkeypatch.setattr(flow, "consultar_mind7_cpf" if stage == "enrichment" else "consultar_c6_cpf", lookup)
    handler = worker._enriquecer_contato_single if stage == "enrichment" else worker._simular_c6_single
    handler(contact_id, Mock())
    with factory() as db:
        contact = db.get(Contact, contact_id)
        if stage == "enrichment":
            assert contact.enrichment_status == "PROCESSANDO" and contact.enrichment_attempts == 2
            assert contact.birth_date is None
        else:
            assert contact.c6_status == "APROVADO" and contact.c6_attempts == 2
            assert contact.c6_financed_value == "R$ 25.000,00"


@pytest.mark.parametrize("status_field,status", [("enrichment_status", "PROCESSANDO"), ("credline_status", "PROCESSANDO"),
                                                ("c6_status", "PROCESSANDO"), ("c6_status", "AGUARDANDO RESULTADO")])
@pytest.mark.parametrize("payload", [{"cpf": "98765432100"}, {"birth_date": "1991-01-01"},
                                   {"phone": "45999990001"}, {"retry_c6": True}])
def test_api_rejects_identity_edit_or_retry_during_query(query_case, status_field, status, payload):
    factory, contact_id, _ = query_case
    change(factory, contact_id, **{status_field: status})
    with factory() as db:
        with pytest.raises(HTTPException) as exc:
            edit_contact(contact_id, ContactUpdate(**payload), db)
        assert exc.value.status_code == 409
        assert "Aguarde a consulta" in exc.value.detail
        assert getattr(db.get(Contact, contact_id), status_field) == status


def test_edit_checks_current_state_even_with_stale_session(query_case):
    factory, contact_id, _ = query_case
    with factory() as db:
        contact = db.get(Contact, contact_id)
        change(factory, contact_id, c6_status="AGUARDANDO RESULTADO")
        assert contact.c6_status == "PENDENTE"
        with pytest.raises(ContactQueryInProgress):
            update_contact(db, contact_id, {"phone": "45999990001"})
    with factory() as db:
        contact = db.get(Contact, contact_id)
        assert contact.phone == "45999990000" and contact.c6_status == "AGUARDANDO RESULTADO"


def test_uncertain_submission_needs_explicit_retry_to_change_cpf(query_case):
    factory, contact_id, _ = query_case
    change(factory, contact_id, c6_status="CONFERIR RESULTADO")
    with factory() as db:
        with pytest.raises(ContactQueryInProgress):
            update_contact(db, contact_id, {"cpf": "98765432100"})
        contact = update_contact(db, contact_id, {"cpf": "98765432100", "retry_c6": True})
        assert contact.cpf == "98765432100" and contact.c6_status == "PENDENTE"


def test_non_identity_edit_preserves_uncertain_result(query_case):
    factory, contact_id, _ = query_case
    change(factory, contact_id, c6_status="AGUARDANDO RESULTADO", c6_financed_value="R$ 25.000,00")
    with factory() as db:
        contact = update_contact(db, contact_id, {"clean_name": "Nome corrigido"})
        assert contact.clean_name == "Nome corrigido"
        assert contact.c6_status == "AGUARDANDO RESULTADO" and contact.c6_financed_value == "R$ 25.000,00"
