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


def test_cycle_pauses_after_completion_without_fixed_maximum(tmp_path):
    clock = [1000.0]
    sleeps = []
    sends = []
    def wait(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    ledger = tmp_path / "rate.sqlite"
    flag = tmp_path / "blocked"
    for _ in range(41):
        quota.reserve_mind7_request(
            ledger_file=ledger, block_file=flag,
            clock=lambda: clock[0], sleep=wait,
            send=lambda: sends.append(clock[0]),
        )
    with sqlite3.connect(ledger) as db:
        timestamps = [r[0] for r in db.execute("SELECT sent_at FROM requests ORDER BY sent_at")]
    gaps = [max(0.9, quota.PAUSE_CYCLE_SECONDS[i % 4] + quota.JITTER_SECONDS[i % 4])
            for i in range(40)]
    assert sends[0] == 1000
    assert timestamps[-1] == pytest.approx(1000 + sum(gaps))
    assert len(timestamps) == 41
    assert all(b - a >= gap - 1e-9 for (a, b), gap in zip(zip(sends, sends[1:]), gaps))
    assert sends[-1] - sends[0] < 300  # 41 envios simulados, sem teto fixo
    assert len(sleeps) > 0


def test_jitter_varies_and_cooldown_extends_the_pause(tmp_path, monkeypatch):
    monkeypatch.setattr(quota, "COOLDOWN_EVERY", 3)
    now, sends = [1000.0], []
    ledger, flag = tmp_path / "rate.sqlite", tmp_path / "blocked"
    def wait(seconds):
        now[0] += seconds
    for _ in range(4):
        quota.reserve_mind7_request(ledger_file=ledger, block_file=flag, clock=lambda: now[0],
                                    sleep=wait, send=lambda: sends.append(now[0]))
    gaps = [b - a for a, b in zip(sends, sends[1:])]
    assert len(gaps) == 3
    assert gaps[0] != gaps[2]  # ciclo com jitter, previsível mas não fixo
    assert gaps[2] >= max(0.9, quota.PAUSE_CYCLE_SECONDS[2] + quota.JITTER_SECONDS[2]) \
        + quota.COOLDOWN_SECONDS[0]


def test_shared_quota_serializes_concurrent_submissions(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(quota, "PAUSE_CYCLE_SECONDS", (0,))
    monkeypatch.setattr(quota, "JITTER_SECONDS", (0,))
    ledger = tmp_path / "rate.sqlite"
    flag = tmp_path / "blocked"
    def reserve(_):
        quota.reserve_mind7_request(
            ledger_file=ledger, block_file=flag, clock=time.time)

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
    assert calls == [pytest.approx(1032.2)]
    with sqlite3.connect(ledger) as db:
        assert db.execute("SELECT sent_at FROM requests ORDER BY sent_at").fetchall() == [(1030.0,), (pytest.approx(1032.2),)]


def test_restart_preserves_next_pause_and_cycle_position(tmp_path):
    now = [1000.0]
    ledger, flag = tmp_path / "quota.sqlite", tmp_path / "blocked"
    sends = []
    def wait(seconds):
        now[0] += seconds
    options = dict(ledger_file=ledger, block_file=flag, clock=lambda: now[0], sleep=wait)
    for _ in range(2):
        quota.reserve_mind7_request(send=lambda: sends.append(now[0]), **options)
    with sqlite3.connect(ledger) as db:
        assert db.execute("SELECT next_index, ready_at FROM cadence").fetchall() == [(2, pytest.approx(1005.55))]
    quota.reserve_mind7_request(send=lambda: sends.append(now[0]), **options)
    assert sends == [1000.0, pytest.approx(1002.2), pytest.approx(1005.55)]


def test_existing_ledger_preserves_cadence_during_migration(tmp_path):
    now = [1000.0]
    ledger, flag = tmp_path / "quota.sqlite", tmp_path / "blocked"
    with sqlite3.connect(ledger) as db:
        db.execute("CREATE TABLE requests (sent_at REAL NOT NULL)")
        db.executemany("INSERT INTO requests VALUES (?)", [(1000.0,)] * 40)
    def wait(seconds):
        now[0] += seconds
    sends = []
    quota.reserve_mind7_request(
        ledger_file=ledger, block_file=flag, clock=lambda: now[0], sleep=wait,
        send=lambda: sends.append(now[0]))
    assert sends == [pytest.approx(1002.2)]


def test_loading_time_finishes_before_cooldown_starts(tmp_path):
    now = [1000.0]
    ledger, flag = tmp_path / "quota.sqlite", tmp_path / "blocked"
    def wait(seconds):
        now[0] += seconds
    def complete_response():
        now[0] += 30
        return "response"
    options = dict(ledger_file=ledger, block_file=flag, clock=lambda: now[0], sleep=wait)
    assert quota.reserve_mind7_request(send=complete_response, **options) == "response"
    sends = []
    quota.reserve_mind7_request(send=lambda: sends.append(now[0]), **options)
    assert sends == [pytest.approx(1032.2)]


def test_block_or_cancel_while_waiting_prevents_send(tmp_path):
    for cancel in (False, True):
        ledger = tmp_path / f"quota-{cancel}.sqlite"
        flag = tmp_path / f"blocked-{cancel}"
        stop = threading.Event()
        now = [1000.0]
        quota.reserve_mind7_request(ledger_file=ledger, block_file=flag, clock=lambda: now[0])
        def wait(seconds):
            now[0] += seconds
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
                                        block_file=flag, clock=lambda: now[0], sleep=wait, send=send)

        send.assert_not_called()


def test_independent_processes_share_cadence_and_restart_state(tmp_path):
    script = '''
from pathlib import Path
import sys, time
from src.automation import mind7_rate_limit as q
q.PAUSE_CYCLE_SECONDS = (0.2,)
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
                                     "Too many requests", "Access denied"])
def test_block_and_challenge_stop_before_navigation(message):
    tab = Mock(url="https://mind-7.org/painel/")
    tab.ele.return_value = SimpleNamespace(raw_text=message)
    with pytest.raises(quota.Mind7AccessBlocked):
        scraper.consultar_mind7_cpf(Mock(), "12345678901", tab=tab)
    tab.get.assert_not_called()
    assert quota.BLOCK_FILE.exists()


def test_challenge_text_without_page_signals_pauses_without_marker():
    tab = Mock(url="https://mind-7.org/painel/")
    tab.ele.return_value = SimpleNamespace(raw_text="Verify you are human")
    with pytest.raises(scraper.Mind7Unavailable):
        scraper.consultar_mind7_cpf(Mock(), "12345678901", tab=tab)
    assert not quota.BLOCK_FILE.exists()



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

def test_challenge_resolves_without_persistent_block(monkeypatch):
    monkeypatch.setattr(scraper, "CHALLENGE_WAIT_SECONDS", 5)
    textos = iter(["executando verificação", "", "MIND7 Consultas"])
    tab = SimpleNamespace(title="Um momento...", html="<div id='challenge-stage'></div>",
                          ele=lambda *a, **kw: SimpleNamespace(raw_text=next(textos, "")))
    scraper._verificar_bloqueio_mind7(tab)
    assert not quota.BLOCK_FILE.exists()


def test_probe_requires_form_and_clears_stale_inflight(tmp_path):
    tab = SimpleNamespace(url="https://mind-7.org/painel/consultas/celular/", title="MIND7",
                          html="", get=lambda url: None,
                          ele=lambda sel, **kw: object() if sel == "#documento" else SimpleNamespace(raw_text=""))
    assert scraper.probe_mind7_access(None, tab=tab)
    assert not quota.BLOCK_FILE.exists()


def test_clear_block_removes_marker_and_abandoned_inflight(tmp_path):
    flag, ledger = tmp_path / "blocked", tmp_path / "quota.sqlite"
    flag.write_text("bloqueio", encoding="utf-8")
    ledger.with_suffix(".inflight").write_text("x", encoding="utf-8")
    quota.clear_mind7_block(block_file=flag, ledger_file=ledger)
    assert not flag.exists() and not ledger.with_suffix(".inflight").exists()

def test_cpf_without_leading_zeros_is_confirmed_and_cpf_is_not_cep():
    texto = ("🆔 CPF: 18248055\nCPF: 18248055\nData de Nascimento: 10/03/1978\n"
             "R JAGUARI, Nº 28, VILA COLATO, HORIZONTINA/RS, 98920-000")
    dados = scraper.extrair_dados_cpf(texto, "00018248055")
    assert dados["cpf"] == "00018248055"
    assert dados["birth_date"] == "1978-03-10"
    assert (dados["cep"], dados["house_number"]) == ("98920000", "28")
    with pytest.raises(ValueError):
        scraper.extrair_dados_cpf(texto, "11111111111")


def test_phone_candidate_name_stops_at_next_section():
    texto = ("CPF: 18248055 | MARCIA ADRIANE DOS SANTOS\n\n\n--- TELEFONES ---\n"
             "(55) 99999-0000\nR JAGUARI, 28, 98920-000")
    candidatos = scraper._extrair_candidatos_mind7(texto)
    assert candidatos[0]["cpf"] == "00018248055"
    assert candidatos[0]["nome"] == "MARCIA ADRIANE DOS SANTOS"

def test_age_uses_portal_hint_or_birth_date():
    com_idade = "CPF: 373.913.992-72\nNASCIMENTO\n02/08/1970 (56 anos)"
    assert scraper.extrair_dados_cpf(com_idade, "37391399272")["age"] == "56"
    so_data = "CPF: 373.913.992-72\nNASCIMENTO: 02/08/1970"
    assert scraper.extrair_dados_cpf(so_data, "37391399272")["age"].isdigit()
