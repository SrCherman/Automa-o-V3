"""A suíte automatizada nunca acessa portais nem usa o estado Mind7 de produção."""
import socket

import pytest


@pytest.fixture(autouse=True)
def offline_mind7_state(tmp_path, monkeypatch):
    from src.automation import mind7_rate_limit as quota
    from src.automation import mind7_scraper as scraper
    from src.services import orchestrator

    flag = tmp_path / "mind7-blocked"
    monkeypatch.setattr(quota, "BLOCK_FILE", flag)
    monkeypatch.setattr(quota, "LEDGER_FILE", tmp_path / "mind7-quota.sqlite")
    monkeypatch.setattr(scraper, "CHALLENGE_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(orchestrator, "MIND7_ACCESS_BLOCK_FILE", flag)

    def refuse_network(*args, **kwargs):
        raise AssertionError("Rede desabilitada na suíte offline; use mocks.")

    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse_network)
