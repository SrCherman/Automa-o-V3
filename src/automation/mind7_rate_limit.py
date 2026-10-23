"""Coordenador de consultas Mind7: uma solicitação por vez entre todos os processos.

Aguarda a resposta e aplica pausas de 1, 2, 1 e 3 s em ciclo persistente.
Bloqueios do portal interrompem novas consultas; sem evasão ou reenvios incertos.
"""
from pathlib import Path
from contextlib import closing
import sqlite3
import time

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
BLOCK_FILE = DATA_DIR / "mind7_access_blocked.txt"
LEDGER_FILE = DATA_DIR / "mind7_query_quota.sqlite"
INFLIGHT_MAX_AGE_SECONDS = 600.0
REQUEST_RETENTION_SECONDS = 86400.0  # Histórico local para diagnóstico, sem quota fixa.
# ~21 consultas/min: abaixo das 115/5min que dispararam o bloqueio do provedor.
PAUSE_CYCLE_SECONDS = (2.2, 3.0, 2.4, 3.6)
JITTER_SECONDS = (0.0, 0.35, -0.25, 0.2)
COOLDOWN_EVERY = 50
COOLDOWN_SECONDS = (12.0, 22.0)


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
            conn.execute("CREATE TABLE IF NOT EXISTS cadence ("
                         "id INTEGER PRIMARY KEY CHECK (id = 1), "
                         "next_index INTEGER NOT NULL, ready_at REAL NOT NULL, "
                         "total INTEGER NOT NULL DEFAULT 0)")
            if "total" not in {row[1] for row in conn.execute("PRAGMA table_info(cadence)")}:
                conn.execute("ALTER TABLE cadence ADD COLUMN total INTEGER NOT NULL DEFAULT 0")
            now = now_fn()
            conn.execute("DELETE FROM requests WHERE sent_at <= ?",
                         (now - REQUEST_RETENTION_SECONDS,))
            latest = conn.execute("SELECT MAX(sent_at) FROM requests").fetchone()[0]
            cadence = conn.execute("SELECT next_index, ready_at, total FROM cadence WHERE id = 1").fetchone()
            next_index, ready_at, total = cadence or (
                0, latest + PAUSE_CYCLE_SECONDS[0] if latest is not None else 0, 0)
            next_time = max(now, ready_at)
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
                if inflight.exists() and now - inflight.stat().st_mtime > INFLIGHT_MAX_AGE_SECONDS:
                    inflight.unlink()
                if inflight.exists():
                    mark_mind7_blocked("Envio anterior interrompido; conferir antes de retomar", block_file=blocked)
                    raise Mind7AccessBlocked("Envio Mind7 anterior sem confirmação; revisão necessária.")
                inflight.write_text("Envio Mind7 em andamento; não remover sem revisão.", encoding="utf-8")
                # Manter o lock durante o envio evita rajadas após atrasos do navegador.
                try:
                    return send() if send is not None else None
                finally:
                    completed_at = now_fn()
                    conn.execute("INSERT INTO requests (sent_at) VALUES (?)", (completed_at,))
                    total += 1
                    cooldown = (COOLDOWN_SECONDS[total % 2]
                                if total % COOLDOWN_EVERY == 0 else 0.0)
                    delay = max(0.9, PAUSE_CYCLE_SECONDS[next_index] + JITTER_SECONDS[next_index])
                    conn.execute("INSERT OR REPLACE INTO cadence (id, next_index, ready_at, total) "
                                 "VALUES (1, ?, ?, ?)", (
                                     (next_index + 1) % len(PAUSE_CYCLE_SECONDS),
                                     completed_at + delay + cooldown, total))
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


def clear_mind7_block(*, block_file=None, ledger_file=None):
    """Retoma após verificação real do portal: remove marcador e envio incerto antigo."""
    blocked = Path(block_file) if block_file is not None else BLOCK_FILE
    ledger = Path(ledger_file) if ledger_file is not None else LEDGER_FILE
    blocked.unlink(missing_ok=True)
    ledger.with_suffix(".inflight").unlink(missing_ok=True)
