import time
from datetime import datetime

import requests


BASE_URL = "http://127.0.0.1:8000"
INITIAL_DELAY_SECONDS = 10
LOOP_SECONDS = 60
IDLE_SECONDS = 5


def log(message: str):
    print(f"[AUTO {datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def request(method: str, path: str, timeout: int = 30):
    return requests.request(method, f"{BASE_URL}{path}", timeout=timeout)


def wait_fastapi():
    while True:
        try:
            response = request("GET", "/status", timeout=5)
            if response.status_code == 200:
                log("FastAPI online.")
                return
        except Exception:
            pass
        log("Aguardando FastAPI...")
        time.sleep(5)


def cycle():
    try:
        state = request("GET", "/automation/state", timeout=5).json()
        if not state.get("armed"):
            log("Automacao desarmada. Aguardando Start no painel.")
            return False

        preflight = request("GET", "/preflight", timeout=10).json()
        mind7_status = preflight.get("mind7_debug", {}).get("status")
        itau_status = preflight.get("itau_debug", {}).get("status")
        pendentes = int(preflight.get("pendentes", 0))

        log(f"Preflight: mind7={mind7_status}, itau={itau_status}, pendentes={pendentes}")

        if mind7_status != "online" or itau_status != "online":
            log("Mind7 ou Credline ainda nao estao prontos. Aguardando...")
            return False

        status = request("GET", "/status", timeout=10).json()
        pendentes = int(status.get("pendentes", 0))
        processando = bool(status.get("processando"))

        if pendentes > 0 and not processando:
            started = request("POST", "/processar", timeout=10).json()
            log(f"Processamento acionado: {started}")
        elif processando:
            log("Processamento ja esta em andamento.")
        else:
            log("Nada para processar agora.")
        return True
    except Exception as exc:
        log(f"Erro no ciclo automatico: {exc}")
        return False


def main():
    log("Auto-orquestrador iniciado.")
    wait_fastapi()
    log(f"Aguardando {INITIAL_DELAY_SECONDS}s iniciais antes do primeiro ciclo.")
    time.sleep(INITIAL_DELAY_SECONDS)

    while True:
        executed = cycle()
        time.sleep(LOOP_SECONDS if executed else IDLE_SECONDS)


if __name__ == "__main__":
    main()
