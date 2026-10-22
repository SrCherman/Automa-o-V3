import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.connection import Base
from src.database.crud import (ContactQueryInProgress, bulk_update_contacts_status,
                               transfer_contacts_to_campaign, update_contact, delete_contact,
                               bulk_delete_contacts, delete_all_contacts, delete_campaign, reset_database)
from src.database.models import Campaign, Contact
from src.api.routes_contacts import (BulkDeletePayload, DeleteAllPayload, remove_contact,
                                     bulk_remove_contacts, delete_all_leads_endpoint)
from src.api.routes_campaigns import remove_campaign
from src.api.routes_automation import ResetPayload, reset_db_endpoint


ACTIVE_STATUSES = [
    ("credline_status", "PROCESSANDO"),
    ("enrichment_status", "PROCESSANDO"),
    ("c6_status", "PROCESSANDO"),
    ("c6_status", "AGUARDANDO RESULTADO"),
]


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'status_guards.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        db.add(Campaign(id=1, name="Synthetic status test"))
        db.flush()
        db.add_all([
            Contact(id=i, campaign_id=1, raw_phone=str(i), phone=str(i), cpf=str(i),
                    clean_name="Synthetic contact", credline_status="CPF COLETADO")
            for i in (1, 2)
        ])
        db.commit()
    yield factory
    engine.dispose()


def set_status(factory, field, status):
    with factory() as db:
        db.query(Contact).filter(Contact.id == 1).update({field: status}, synchronize_session=False)
        db.commit()


@pytest.mark.parametrize("field,status", ACTIVE_STATUSES)
def test_status_edit_cannot_remove_active_query_guard(database, field, status):
    set_status(database, field, status)
    with database() as db:
        with pytest.raises(ContactQueryInProgress):
            update_contact(db, 1, {field: "PENDENTE"})
        assert getattr(db.get(Contact, 1), field) == status
        with pytest.raises(ContactQueryInProgress):
            update_contact(db, 1, {"cpf": "3"})
        assert db.get(Contact, 1).cpf == "1"


@pytest.mark.parametrize("field,status", ACTIVE_STATUSES)
def test_identical_active_status_allows_name_edit(database, field, status):
    set_status(database, field, status)
    with database() as db:
        contact = update_contact(db, 1, {field: status, "clean_name": "Corrected synthetic name"})
        assert getattr(contact, field) == status
        assert contact.clean_name == "Corrected synthetic name"


@pytest.mark.parametrize("field,status", ACTIVE_STATUSES)
def test_stale_session_cannot_overwrite_new_active_status(database, field, status):
    with database() as db:
        contact = db.get(Contact, 1)
        previous_status = getattr(contact, field)
        set_status(database, field, status)
        assert getattr(contact, field) == previous_status
        with pytest.raises(ContactQueryInProgress):
            update_contact(db, 1, {field: previous_status, "clean_name": "Must not be saved"})
        assert getattr(db.get(Contact, 1), field) == status
        assert db.get(Contact, 1).clean_name == "Synthetic contact"


def test_bulk_status_change_rolls_back_entire_batch_if_any_query_is_active(database):
    set_status(database, "credline_status", "PROCESSANDO")
    with database() as db:
        with pytest.raises(ContactQueryInProgress):
            bulk_update_contacts_status(db, [1, 2], "PENDENTE")
        assert db.get(Contact, 1).credline_status == "PROCESSANDO"
        assert db.get(Contact, 2).credline_status == "CPF COLETADO"


def test_bulk_identical_active_status_is_allowed(database):
    set_status(database, "credline_status", "PROCESSANDO")
    with database() as db:
        assert bulk_update_contacts_status(db, [1], "PROCESSANDO") == 1
        assert db.get(Contact, 1).credline_status == "PROCESSANDO"


def test_bulk_idle_status_preserves_duplicate_and_missing_id_behavior(database):
    with database() as db:
        assert bulk_update_contacts_status(db, [1, 1, 999], "pendente") == 1
        assert db.get(Contact, 1).credline_status == "PENDENTE"
        assert db.get(Contact, 2).credline_status == "CPF COLETADO"


def test_transfer_waits_for_active_query_and_rolls_back_whole_batch(database):
    with database() as db:
        db.add(Campaign(id=2, name="Destination"))
        db.commit()
    set_status(database, "c6_status", "AGUARDANDO RESULTADO")
    with database() as db:
        with pytest.raises(ContactQueryInProgress):
            transfer_contacts_to_campaign(db, [1, 2], 2)
        assert [db.get(Contact, cid).campaign_id for cid in (1, 2)] == [1, 1]


DELETIONS = [
    pytest.param(lambda db: delete_contact(db, 1), id="single"),
    pytest.param(lambda db: bulk_delete_contacts(db, [1, 2]), id="bulk"),
    pytest.param(lambda db: delete_all_contacts(db, 1), id="campaign-contacts"),
    pytest.param(lambda db: delete_all_contacts(db), id="all-contacts"),
    pytest.param(lambda db: delete_campaign(db, 1), id="campaign"),
    pytest.param(lambda db: reset_database(db, 1), id="campaign-reset"),
    pytest.param(lambda db: reset_database(db), id="global-reset"),
]


@pytest.mark.parametrize("field,status", ACTIVE_STATUSES)
@pytest.mark.parametrize("delete", DELETIONS)
def test_deletion_preserves_all_contacts_while_any_query_is_active(database, field, status, delete):
    set_status(database, field, status)
    with database() as db:
        with pytest.raises(ContactQueryInProgress):
            delete(db)
        assert db.query(Contact).count() == 2
        assert db.get(Campaign, 1) is not None
        assert getattr(db.get(Contact, 1), field) == status


@pytest.mark.parametrize("delete", DELETIONS)
def test_deletion_still_works_when_contacts_are_idle(database, delete):
    with database() as db:
        delete(db)
        assert db.get(Contact, 1) is None


API_DELETIONS = [
    pytest.param(lambda db: remove_contact(1, db=db), id="single"),
    pytest.param(lambda db: bulk_remove_contacts(BulkDeletePayload(contact_ids=[1, 2]), db=db), id="bulk"),
    pytest.param(lambda db: delete_all_leads_endpoint(DeleteAllPayload(campaign_id=1), db=db), id="all-contacts"),
    pytest.param(lambda db: remove_campaign(1, db=db), id="campaign"),
    pytest.param(lambda db: reset_db_endpoint(ResetPayload(campaign_id=1, confirm=True), db=db), id="reset"),
]


@pytest.mark.parametrize("delete", API_DELETIONS)
def test_delete_api_returns_conflict_for_active_query(database, delete):
    set_status(database, "c6_status", "AGUARDANDO RESULTADO")
    with database() as db:
        with pytest.raises(HTTPException) as exc:
            delete(db)
        assert exc.value.status_code == 409
        assert db.query(Contact).count() == 2
