"""
Smoke test do fluxo manual de consultas.
Verifica planilha, painel local e navegadores de consulta.
"""

import os
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = r"C:\Users\Carlos\Desktop\AUTOMAÇÃO consultas\credit_pipeline"
sys.path.insert(0, BASE_DIR)


def ok(label: str, detail: str = ""):
    print(f"[PASS] {label}" + (f" - {detail}" if detail else ""))


def fail(label: str, detail: str = ""):
    print(f"[FAIL] {label}" + (f" - {detail}" if detail else ""))


def check(label: str, cond: bool, detail: str = ""):
    if cond:
        ok(label, detail)
    else:
        fail(label, detail)


def main():
    print("=" * 60)
    print("SMOKE TEST - CONSULTAS MANUAIS")
    print("=" * 60)

    try:
        from app.services.planilha import FILE_PATH, listar_pendentes

        check("Planilha localizada", os.path.exists(FILE_PATH), FILE_PATH)
        check("Leitura de pendentes", isinstance(listar_pendentes(), list), "listar_pendentes() retornou lista")
    except Exception as exc:
        fail("Planilha/pendentes", str(exc))

    try:
        r = requests.get("http://127.0.0.1:8000/status", timeout=5)
        data = r.json()
        check("FastAPI online", r.status_code == 200, str(data))
    except Exception as exc:
        fail("FastAPI online", str(exc))

    try:
        r = requests.get("http://127.0.0.1:8000/automation/state", timeout=5)
        data = r.json()
        check("Estado da automacao acessivel", r.status_code == 200, str(data))
    except Exception as exc:
        fail("Estado da automacao acessivel", str(exc))

    try:
        r = requests.get("http://127.0.0.1:8000/preflight", timeout=8)
        data = r.json()
        check("Preflight acessivel", r.status_code == 200, str(data))
    except Exception as exc:
        fail("Preflight acessivel", str(exc))

    print("=" * 60)
    print("Fim do smoke test")


if __name__ == "__main__":
    main()
