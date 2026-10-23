"""Quota de consultas Mind7: compartilhada entre etapas, processos e reinícios.

Aplica um teto conservador (20 consultas/5min) e intervalo mínimo (15 s).
Não contorna bloqueios: a presença do aviso interrompe novas submissões.
"""
from pathlib import Path
from contextlib import closing
import sqlite3
import time

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
BLOCK_FILE = DATA_DIR / "mind7_access_blocked.txt"
LEDGER_FILE = DATA_DIR / "mind7_query_quota.sqlite"
WINDOW_SECONDS = 300.0
MAX_PER_WINDOW = 20
MIN_INTERVAL_SECONDS = 15.0


class Mind7AccessBlocked(RuntimeError):
    pass


class Mind7QueryCancelled(RuntimeError):
    pass


def ensure_mind7_allowed(stop_event=None):
    if BLOCK_FILE.exists():
        raise Mind7AccessBlocked("Mind7 bloqueado: aguarde liberação do provedor.")
    if stop_event is not None and stop_event.is_set():
        raise Mind7QueryCancelled("Consulta cancelada antes do envio.")


def reserve_mind7_request(stop_event=None, *, ledger_file=None, block_file=None,
                          clock=None, sleep=None, send=None):
    """Serializa o envio; conta falhas e pausa se um processo morrer durante o envio."""
    ledger = Path(ledger_file) if ledger_file is not None else LEDGER_FILE
    blocked = Path(block_file) if block_file is not None else BLOCK_FILE
    now_fn = clock or time.time
    sleep_fn = sleep or time.sleep
    ledger.parent.mkdir(parents=True, exist_ok=True)

    while True:
        if blocked.exists():
            raise Mind7AccessBlocked("Mind7 bloqueado: aguarde liberação do provedor.")
        if stop_event is not None and stop_event.is_set():
            raise Mind7QueryCancelled("Consulta cancelada antes do envio.")

        with closing(sqlite3.connect(ledger, timeout=0.25, isolation_level=None)) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                if stop_event is not None:
                    if stop_event.wait(0.25):
                        raise Mind7QueryCancelled("Consulta cancelada antes do envio.")
                else:
                    sleep_fn(0.25)
                continue
            conn.execute("CREATE TABLE IF NOT EXISTS requests (sent_at REAL NOT NULL)")
            now = now_fn()
            conn.execute("DELETE FROM requests WHERE sent_at <= ?", (now - WINDOW_SECONDS,))
            previous = [r[0] for r in conn.execute(
                "SELECT sent_at FROM requests ORDER BY sent_at").fetchall()]
            next_time = now
            if previous:
                next_time = max(next_time, previous[-1] + MIN_INTERVAL_SECONDS)
            if len(previous) >= MAX_PER_WINDOW:
                next_time = max(next_time, previous[-MAX_PER_WINDOW] + WINDOW_SECONDS)
            if next_time <= now:
                # Verificar novamente dentro da transação, inclusive após aguardar o lock.
                if blocked.exists():
                    conn.execute("ROLLBACK")
                    raise Mind7AccessBlocked("Mind7 bloqueado: nenhum envio permitido.")
                if stop_event is not None and stop_event.is_set():
                    conn.execute("ROLLBACK")
                    raise Mind7QueryCancelled("Consulta cancelada antes do envio.")
                # Um processo encerrado antes do commit deixa evidência de envio incerto.
                inflight = ledger.with_suffix(".inflight")
                if inflight.exists():
                    mark_mind7_blocked("Envio anterior interrompido; conferir antes de retomar", block_file=blocked)
                    raise Mind7AccessBlocked("Envio Mind7 anterior sem confirmação; revisão necessária.")
                inflight.write_text("Envio Mind7 em andamento; não remover sem revisão.", encoding="utf-8")
                # Manter o lock durante o envio evita rajadas após atrasos do navegador.
                try:
                    return send() if send is not None else None
                finally:
                    conn.execute("INSERT INTO requests (sent_at) VALUES (?)", (now_fn(),))
                    conn.execute("COMMIT")
                    inflight.unlink()
            conn.execute("COMMIT")

        delay = min(1.0, max(0.01, next_time - now_fn()))
        if stop_event is not None:
            if stop_event.wait(delay):
                raise Mind7QueryCancelled("Consulta cancelada antes do envio.")
        else:
            sleep_fn(delay)


def mark_mind7_blocked(reason="Portal informou bloqueio por excesso de consultas", *, block_file=None):
    """Pausa persistente; nunca substitui o registro original."""
    blocked = Path(block_file) if block_file is not None else BLOCK_FILE
    blocked.parent.mkdir(parents=True, exist_ok=True)
    try:
        with blocked.open("x", encoding="utf-8") as marker:
            marker.write(f"Bloqueio Mind7 confirmado: {reason}. Aguardar liberação do provedor.\n")
    except FileExistsError:
        pass
