"""Testes sem rede para quota Mind7 compartilhada entre etapas e processos."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path
import subprocess
import sys

import pytest

from src.automation import mind7_rate_limit as quota
from src.automation import mind7_scraper as scraper


def test_quota_limits_20_requests_per_5_min_and_15s_spacing(tmp_path):
    clock = [1000.0]
    sleeps = []
    def wait(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    ledger = tmp_path / "rate.sqlite"
    flag = tmp_path / "blocked"
    for i in range(21):
        quota.reserve_mind7_request(
            ledger_file=ledger, block_file=flag,
            clock=lambda: clock[0], sleep=wait,
        )
    with sqlite3.connect(ledger) as db:
        timestamps = [r[0] for r in db.execute("SELECT sent_at FROM requests ORDER BY sent_at")]
    assert timestamps[-1] == pytest.approx(1300.0)
    assert len(timestamps) == 20
    assert all(b - a >= 15 for a, b in zip(timestamps, timestamps[1:]))
    assert len(sleeps) > 0


def test_shared_quota_serializes_concurrent_submissions(tmp_path, monkeypatch):
    monkeypatch.setattr(quota, "MIN_INTERVAL_SECONDS", 0)
    ledger = tmp_path / "rate.sqlite"
    flag = tmp_path / "blocked"
    def reserve(_):
        quota.reserve_mind7_request(
            ledger_file=ledger, block_file=flag, clock=lambda: 1000.0)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(reserve, range(8)))
    with sqlite3.connect(ledger) as db:
        count = db.execute("SELECT count(*) FROM requests").fetchone()[0]
    assert count == 8


def test_blocked_and_cancelled_never_reserve(tmp_path):
    ledger = tmp_path / "rate.sqlite"
    flag = tmp_path / "blocked"
    flag.write_text("Acesso bloqueado", encoding="utf-8")
    with pytest.raises(quota.Mind7AccessBlocked):
        quota.reserve_mind7_request(ledger_file=ledger, block_file=flag)
    assert not ledger.exists()
    flag.unlink()
    stop = threading.Event()
    stop.set()
    with pytest.raises(quota.Mind7QueryCancelled):
        quota.reserve_mind7_request(stop, ledger_file=ledger, block_file=flag)
    assert not ledger.exists()


def test_portal_block_message_creates_persistent_stop(tmp_path, monkeypatch):
    flag = tmp_path / "blocked"
    monkeypatch.setattr(quota, "BLOCK_FILE", flag)
    tab = SimpleNamespace(ele=lambda *a, **kw: SimpleNamespace(
        raw_text="Bloqueio automático: 115 consultas em 5 min"))
    with pytest.raises(quota.Mind7AccessBlocked):
        scraper._verificar_bloqueio_mind7(tab)
    assert flag.exists()


def test_failed_send_counts_and_delayed_send_keeps_spacing(tmp_path):
    now = [1000.0]
    ledger, flag = tmp_path / "quota.sqlite", tmp_path / "blocked"
    def wait(seconds):
        now[0] += seconds
    def uncertain_send():
        now[0] += 30
        raise TimeoutError("Resposta perdida")
    options = dict(ledger_file=ledger, block_file=flag, clock=lambda: now[0], sleep=wait)
    with pytest.raises(TimeoutError):
        quota.reserve_mind7_request(send=uncertain_send, **options)
    calls = []
    quota.reserve_mind7_request(send=lambda: calls.append(now[0]), **options)
    assert calls == [1045.0]
    with sqlite3.connect(ledger) as db:
        assert db.execute("SELECT sent_at FROM requests ORDER BY sent_at").fetchall() == [(1030.0,), (1045.0,)]


def test_block_or_cancel_while_waiting_prevents_send(tmp_path):
    for cancel in (False, True):
        ledger = tmp_path / f"quota-{cancel}.sqlite"
        flag = tmp_path / f"blocked-{cancel}"
        stop = threading.Event()
        quota.reserve_mind7_request(ledger_file=ledger, block_file=flag, clock=lambda: 1000)
        def wait(seconds):
            if cancel:
                stop.set()
            else:
                flag.write_text("Bloqueado", encoding="utf-8")
        send = Mock()
        # No stop_event here so the deterministic sleep hook can trigger the flag.
        if cancel:
            def cancelled_wait(seconds):
                wait(seconds)
                return True
            stop.wait = cancelled_wait
        with pytest.raises(quota.Mind7QueryCancelled if cancel else quota.Mind7AccessBlocked):
            quota.reserve_mind7_request(stop if cancel else None, ledger_file=ledger,
                                        block_file=flag, clock=lambda: 1000, sleep=wait, send=send)
        send.assert_not_called()


def test_independent_processes_share_spacing_window_and_restart_state(tmp_path):
    script = '''
from pathlib import Path
import sys, time
from src.automation import mind7_rate_limit as q
q.MIN_INTERVAL_SECONDS = 0.2
q.MAX_PER_WINDOW = 2
q.WINDOW_SECONDS = 0.8
root = Path(sys.argv[1])
def send():
    with (root / "events").open("a") as out:
        out.write(str(time.time()) + "\\n")
q.reserve_mind7_request(ledger_file=root / "quota.sqlite", block_file=root / "blocked", send=send)
'''
    command = [sys.executable, "-c", script, str(tmp_path)]
    processes = [subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
    for process in processes:
        output = process.communicate(timeout=20)
        assert process.returncode == 0, output
    # A fresh fifth process must honor the existing ledger too.
    subprocess.run(command, cwd=Path(__file__).resolve().parents[1], check=True, timeout=20)
    timestamps = [float(line) for line in (tmp_path / "events").read_text().splitlines()]
    assert len(timestamps) == 5
    assert all(b - a >= 0.19 for a, b in zip(timestamps, timestamps[1:]))
    assert all(b - a >= 0.79 for a, b in zip(timestamps, timestamps[2:]))


def test_process_death_during_send_requires_persistent_manual_review(tmp_path):
    script = '''
import os, sys
from pathlib import Path
from src.automation import mind7_rate_limit as q
root = Path(sys.argv[1])
q.reserve_mind7_request(ledger_file=root / "quota.sqlite", block_file=root / "blocked", send=lambda: os._exit(9))
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], timeout=20)
    assert result.returncode == 9
    send = Mock()
    with pytest.raises(quota.Mind7AccessBlocked):
        quota.reserve_mind7_request(ledger_file=tmp_path / "quota.sqlite",
                                    block_file=tmp_path / "blocked", send=send)
    send.assert_not_called()
    assert (tmp_path / "blocked").exists()


def test_mark_blocked_never_overwrites_original(tmp_path, monkeypatch):
    flag = tmp_path / "blocked"
    flag.write_text("Registro original", encoding="utf-8")
    monkeypatch.setattr(quota, "BLOCK_FILE", flag)
    quota.mark_mind7_blocked("Outra mensagem")
    assert flag.read_text(encoding="utf-8") == "Registro original"


@pytest.mark.parametrize("message", ["Bloqueio automático", "Limite de consultas",
                                     "Too many requests", "Access denied", "Verify you are human"])
def test_block_and_challenge_stop_before_navigation(message):
    tab = Mock(url="https://mind-7.org/painel/")
    tab.ele.return_value = SimpleNamespace(raw_text=message)
    with pytest.raises(quota.Mind7AccessBlocked):
        scraper.consultar_mind7_cpf(Mock(), "12345678901", tab=tab)
    tab.get.assert_not_called()
    assert quota.BLOCK_FILE.exists()


def test_hidden_loader_does_not_create_a_persistent_block():
    body = SimpleNamespace(text="MIND7 Consultas CPF\nAguarde um momento...",
                           raw_text="MIND7 Consultas CPF")
    tab = SimpleNamespace(title="MIND7 - Consultas", ele=lambda *args, **kwargs: body)
    scraper._verificar_bloqueio_mind7(tab)
    assert not quota.BLOCK_FILE.exists()


@pytest.mark.parametrize("text", ["Aguarde um momento, consultando...", "Just a moment, carregando dados..."])
def test_normal_loading_message_is_not_a_challenge(text):
    body = SimpleNamespace(text=text, raw_text=text)
    tab = SimpleNamespace(title="MIND7 - Consultas", ele=lambda *args, **kwargs: body)
    scraper._verificar_bloqueio_mind7(tab)
    assert not quota.BLOCK_FILE.exists()


@pytest.mark.parametrize("title", ["Just a moment...", "Um momento..."])
def test_active_challenge_title_pauses_without_declaring_provider_block(title):
    body = SimpleNamespace(text="", raw_text="")
    tab = SimpleNamespace(title=title, ele=lambda *args, **kwargs: body)
    with pytest.raises(scraper.Mind7Unavailable):
        scraper._verificar_bloqueio_mind7(tab)
    assert not quota.BLOCK_FILE.exists()
