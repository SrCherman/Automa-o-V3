"""
Ponto de entrada da aplicacao FastAPI.
Fluxo manual:
  1. A planilha Excel recebe nome + telefone preenchidos manualmente
  2. O painel arma a automacao com Start
  3. /processar consulta Mind7 + Credline Itau
  4. Os resultados sao gravados na planilha
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import os
import sys
import threading

import requests
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import HTMLResponse

from app.services.api_client import DirectAPIClient
from app.services.planilha import (
    listar_pendentes,
    marcar_processando,
    atualizar_resultado,
    adicionar_contatos_em_lote,
)
from app.services.scraper import iniciar_navegador, consultar_mind7, consultar_itau_credline
from src.automation.mind7_rate_limit import BLOCK_FILE, Mind7AccessBlocked, ensure_mind7_allowed
from src.automation.mind7_scraper import Mind7Unavailable


app = FastAPI(title="Esteira de Qualificacao de Credito")

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "processamento.log")


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


_log_handle = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
sys.stdout = Tee(sys.stdout, _log_handle)
sys.stderr = Tee(sys.stderr, _log_handle)

api_client = DirectAPIClient()
mind7_browser_lock = threading.Lock()
itau_browser_lock = threading.Lock()
process_lock = threading.Lock()
automation_lock = threading.Lock()
processando = False
automation_armed = False
automation_started_at = None
automation_stopped_at = None


def _ports():
    mind7_port = int(os.getenv("MIND7_DEBUG_PORT", "9223"))
    itau_port = int(os.getenv("ITAU_DEBUG_PORT", "9224"))
    return mind7_port, itau_port


def _processar_pendentes():
    """
    Processa os contatos pendentes da planilha:
      - consulta Mind7
      - consulta Credline Itau
      - atualiza a planilha a cada contato
    """
    global processando
    with process_lock:
        processando = True

    try:
        ensure_mind7_allowed()
        pendentes = listar_pendentes()
        if not pendentes:
            print("[PROCESSADOR] Nenhum contato pendente encontrado.")
            return

        max_workers = max(1, min(int(os.getenv("PROCESS_MAX_WORKERS", "3")), len(pendentes)))
        print(f"[PROCESSADOR] Iniciando consulta de {len(pendentes)} contatos pendentes com {max_workers} worker(s)...")

        mind7_port, itau_port = _ports()

        auth = {}
        try:
            with mind7_browser_lock:
                auth = api_client._obter_auth_do_navegador(mind7_port=mind7_port, itau_port=itau_port)
        except Exception as exc:
            print(f"[PROCESSADOR] Aviso: nao foi possivel ler cookies do navegador: {exc}")

        cookies_mind7 = auth.get("mind7_cookies", "") if isinstance(auth, dict) else ""
        if not cookies_mind7:
            print("[PROCESSADOR] Aviso: cookies do Mind7 nao encontrados. Vou depender da consulta visual.")

        try:
            mind7_page = iniciar_navegador(mind7_port)
        except Exception as exc:
            print(f"[PROCESSADOR] ERRO ao conectar no navegador do Mind7: {exc}")
            mind7_page = None

        try:
            itau_page = iniciar_navegador(itau_port)
        except Exception as exc:
            print(f"[PROCESSADOR] ERRO ao conectar no navegador do Credline Itau: {exc}")
            itau_page = None

        def processar_contato(indice: int, contato: dict):
            row_num = contato["row_num"]
            nome_whats = contato["nome_whatsapp"]
            telefone = contato["telefone"]

            print(f"\n[{indice}/{len(pendentes)}] Consultando: {nome_whats} ({telefone})...")
            marcar_processando(row_num, "Consultando Mind7")

            try:
                dados_mind7 = None

                ensure_mind7_allowed()
                if cookies_mind7:
                    dados_mind7 = api_client.consultar_mind7_api(telefone, cookies_mind7, nome_whats)
                elif mind7_page:
                    print("  Mind7: consulta visual no Edge autenticado.")
                    with mind7_browser_lock:
                        dados_mind7 = consultar_mind7(mind7_page, telefone, nome_whats)
                else:
                    raise Mind7Unavailable("Mind7 sem sessão disponível; consulta pendente.")

                if not dados_mind7:
                    atualizar_resultado(
                        row_num,
                        "",
                        "",
                        "SEM DADOS",
                        "Mind7 nao retornou dados compativeis com nome/telefone",
                    )
                    return

                cpf = dados_mind7["cpf"]
                nome_mind7 = dados_mind7["nome_completo"]
                print(f"  Mind7: {nome_mind7} | CPF: {cpf}")
                
                # Gravar CEP, número e nascimento real em colunas identificadas pelo cabeçalho.
                try:
                    import openpyxl
                    from app.services.planilha import FILE_PATH, _lock, _salvar_planilha
                    with _lock:
                        if os.path.exists(FILE_PATH):
                            wb_temp = openpyxl.load_workbook(FILE_PATH)
                            ws_temp = wb_temp.active
                            ws_temp.cell(row=row_num, column=8, value=dados_mind7.get("cep", ""))
                            ws_temp.cell(row=row_num, column=9, value=dados_mind7.get("numero_casa", ""))
                            nascimento = dados_mind7.get("birth_date", "")
                            if nascimento:
                                try:
                                    datetime.strptime(nascimento, "%Y-%m-%d")
                                except (TypeError, ValueError):
                                    nascimento = ""
                            if nascimento:
                                coluna = next(
                                    (cell.column for cell in ws_temp[1] if cell.value == "Data de Nascimento"),
                                    None,
                                )
                                if coluna is None:
                                    coluna = ws_temp.max_column + 1
                                    ws_temp.cell(row=1, column=coluna, value="Data de Nascimento")
                                if not ws_temp.cell(row=row_num, column=coluna).value:
                                    ws_temp.cell(row=row_num, column=coluna, value=nascimento)
                            _salvar_planilha(wb_temp)
                except Exception as exc:
                    print(f"  Erro ao gravar dados adicionais na planilha: {exc}")

                marcar_processando(row_num, "Consultando Credline Itau")

                if itau_page:
                    try:
                        with itau_browser_lock:
                            resultado_itau = consultar_itau_credline(itau_page, cpf)
                        
                        if isinstance(resultado_itau, dict):
                            status_itau = resultado_itau.get("status")
                            valor_fin_raw = resultado_itau.get("valor")
                            
                            if status_itau in ("NAO CORRENTISTA", "CORRENTISTA"):
                                obs = "Aprovado - Nao e correntista Itau" if status_itau == "NAO CORRENTISTA" else "Aprovado - Ja e correntista Itau"
                                
                                if valor_fin_raw == "ERRO_SIMULACAO":
                                    valor_fin = ""
                                    obs += " (Simulacao falhou)"
                                else:
                                    valor_fin = valor_fin_raw if valor_fin_raw else ""
                                    
                                atualizar_resultado(
                                    row_num,
                                    cpf,
                                    nome_mind7,
                                    status_itau,
                                    obs,
                                )
                                # Gravar valor financiado na coluna 11 (K)
                                if valor_fin:
                                    try:
                                        with _lock:
                                            if os.path.exists(FILE_PATH):
                                                wb_temp = openpyxl.load_workbook(FILE_PATH)
                                                ws_temp = wb_temp.active
                                                ws_temp.cell(row=row_num, column=11, value=valor_fin)
                                                _salvar_planilha(wb_temp)
                                    except Exception as exc:
                                        print(f"  Erro ao gravar valor financiado na planilha: {exc}")
                            else:
                                atualizar_resultado(
                                    row_num,
                                    cpf,
                                    nome_mind7,
                                    "CONSULTADO",
                                    "Reprovado - Nao aprovado Itau",
                                )
                        else:
                            atualizar_resultado(
                                row_num,
                                cpf,
                                nome_mind7,
                                "PENDENTE ITAU",
                                "Credline sem resultado confiavel; tente novamente",
                            )
                    except Exception as exc:
                        atualizar_resultado(row_num, cpf, nome_mind7, "ERRO", f"Erro Itau: {str(exc)[:50]}")
                else:
                    atualizar_resultado(
                        row_num,
                        cpf,
                        nome_mind7,
                        "PENDENTE ITAU",
                        "Mind7 OK, navegador indisponivel para Itau",
                    )
            except (Mind7AccessBlocked, Mind7Unavailable, TimeoutError):
                print("  Mind7 bloqueado: consulta suspensa, sem marcar o contato como sem dados.")
                atualizar_resultado(row_num, "", "", "PENDENTE", "Mind7 indisponível; acesso bloqueado.")
            except Exception as exc:
                print(f"  ERRO ao processar {nome_whats}: {exc}")
                atualizar_resultado(row_num, "", "", "ERRO", str(exc)[:50])

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(processar_contato, indice, contato)
                for indice, contato in enumerate(pendentes, 1)
            ]
            for future in as_completed(futures):
                future.result()

        print(f"\n[PROCESSADOR] Todas as {len(pendentes)} consultas finalizadas!")

    except Exception as exc:
        print(f"[PROCESSADOR] Erro fatal: {exc}")
    finally:
        with process_lock:
            processando = False


@app.post("/processar")
async def iniciar_processamento(background_tasks: BackgroundTasks):
    """
    Dispara o processamento dos contatos pendentes em background.
    """
    global processando
    if BLOCK_FILE.exists():
        return {"status": "blocked", "message": "Mind7 bloqueado. Aguarde a liberação do provedor."}
    with process_lock:
        if processando:
            return {"status": "busy", "message": "Ja existe um processamento em andamento. Aguarde."}
        processando = True

    background_tasks.add_task(_processar_pendentes)
    return {"status": "started", "message": "Processamento iniciado em background. Acompanhe pelo terminal."}


@app.post("/webhook/contatos")
async def webhook_contatos(payload: dict):
    """
    Recebe lote de contatos do bridge WhatsApp e adiciona na planilha.
    Formato esperado: {"contatos": [{"nome": "...", "telefone": "..."}]}
    """
    contatos = payload.get("contatos", [])
    if not isinstance(contatos, list):
        return {"status": "error", "message": "Campo 'contatos' deve ser uma lista."}

    resultado = adicionar_contatos_em_lote(contatos)
    print(f"[WEBHOOK] Lote recebido: {len(contatos)} | resultado={resultado}")
    return {"status": "ok", "recebidos": len(contatos), **resultado}


@app.post("/automation/start")
async def automation_start():
    """Arma a automacao para o orquestrador iniciar o processamento."""
    global automation_armed, automation_started_at, automation_stopped_at
    if BLOCK_FILE.exists():
        return {"status": "blocked", "message": "Mind7 bloqueado. Aguarde a liberação do provedor."}
    with automation_lock:
        automation_armed = True
        automation_started_at = datetime.now().isoformat(timespec="seconds")
        automation_stopped_at = None
    print("[AUTOMACAO] Start acionado pelo painel.")
    return {"status": "armed", "message": "Automacao armada. O orquestrador pode iniciar os ciclos."}


@app.post("/automation/stop")
async def automation_stop():
    """Desarma a automacao para impedir novos ciclos automaticos."""
    global automation_armed, automation_stopped_at
    with automation_lock:
        automation_armed = False
        automation_stopped_at = datetime.now().isoformat(timespec="seconds")
    print("[AUTOMACAO] Stop acionado pelo painel.")
    return {"status": "stopped", "message": "Automacao desarmada."}


@app.get("/automation/state")
async def automation_state():
    """Retorna o estado do painel de controle."""
    with automation_lock:
        armed = automation_armed
        started_at = automation_started_at
        stopped_at = automation_stopped_at
    return {
        "armed": armed and not BLOCK_FILE.exists(),
        "mind7_access_blocked": BLOCK_FILE.exists(),
        "started_at": started_at,
        "stopped_at": stopped_at,
        "processando": processando,
        "pendentes": len(listar_pendentes()),
    }


@app.get("/", response_class=HTMLResponse)
async def root():
    """Painel local simples para iniciar e parar a automacao manualmente."""
    html = """
    <!doctype html>
    <html lang="pt-BR">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Painel Automacao</title>
      <style>
        :root {
          --bg: #0f172a;
          --panel: #111827;
          --line: #334155;
          --text: #e5e7eb;
          --muted: #94a3b8;
          --accent: #22c55e;
          --danger: #ef4444;
          --warn: #f59e0b;
        }
        body {
          margin: 0;
          font-family: Segoe UI, Arial, sans-serif;
          background: linear-gradient(135deg, #0b1020, #111827 55%, #0f172a);
          color: var(--text);
        }
        .wrap { max-width: 920px; margin: 0 auto; padding: 32px 20px 40px; }
        .card {
          background: rgba(17,24,39,.92);
          border: 1px solid var(--line);
          border-radius: 16px;
          padding: 20px;
          box-shadow: 0 20px 50px rgba(0,0,0,.25);
        }
        h1 { margin: 0 0 8px; font-size: 28px; }
        p { color: var(--muted); margin: 0 0 16px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 18px 0; }
        .pill {
          border: 1px solid var(--line);
          border-radius: 12px;
          padding: 14px;
          background: rgba(15,23,42,.75);
        }
        .label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; }
        .value { margin-top: 6px; font-size: 18px; font-weight: 700; }
        .actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }
        button {
          border: 0;
          border-radius: 12px;
          padding: 12px 16px;
          font-weight: 700;
          cursor: pointer;
          color: #fff;
        }
        .start { background: var(--accent); }
        .stop { background: var(--danger); }
        .process { background: #2563eb; }
        .small { font-size: 13px; color: var(--muted); margin-top: 12px; }
        code { color: #93c5fd; }
      </style>
    </head>
    <body>
      <div class="wrap">
        <div class="card">
          <h1>Painel da Automacao</h1>
          <p>Preencha a planilha manualmente com nome e telefone. Clique Start para armar a fila e Processar agora para disparar a consulta.</p>
          <div class="grid" id="stats"></div>
          <div class="actions">
            <button class="start" onclick="post('/automation/start')">Start</button>
            <button class="stop" onclick="post('/automation/stop')">Stop</button>
            <button class="process" onclick="post('/processar')">Processar agora</button>
          </div>
          <div class="small">Endpoints: <code>/automation/state</code>, <code>/automation/start</code>, <code>/automation/stop</code>, <code>/processar</code></div>
        </div>
      </div>
      <script>
        async function refresh() {
          const res = await fetch('/automation/state');
          const data = await res.json();
          const items = [
            ['Armada', data.armed ? 'SIM' : 'NAO'],
            ['Processando', data.processando ? 'SIM' : 'NAO'],
            ['Pendentes', String(data.pendentes ?? 0)],
            ['Start', data.started_at ? String(data.started_at) : '-'],
            ['Stop', data.stopped_at ? String(data.stopped_at) : '-']
          ];
          document.getElementById('stats').innerHTML = items.map(([k, v]) => `
            <div class="pill"><div class="label">${k}</div><div class="value">${v}</div></div>
          `).join('');
        }
        async function post(path) {
          await fetch(path, {method:'POST'});
          await refresh();
        }
        refresh();
        setInterval(refresh, 3000);
      </script>
    </body>
    </html>
    """
    return HTMLResponse(html)


@app.get("/preflight")
async def preflight():
    """Checa os servicos locais necessarios antes de processar."""
    checks = {}
    mind7_port, itau_port = _ports()

    try:
        mind7_tabs = requests.get(f"http://127.0.0.1:{mind7_port}/json", timeout=3).json()
        checks["mind7_debug"] = {
            "status": "online",
            "tabs": len(mind7_tabs),
            "url": next((t.get("url", "") for t in mind7_tabs), ""),
        }
    except Exception as exc:
        checks["mind7_debug"] = {"status": "offline", "error": str(exc)}

    try:
        itau_tabs = requests.get(f"http://127.0.0.1:{itau_port}/json", timeout=3).json()
        checks["itau_debug"] = {
            "status": "online",
            "tabs": len(itau_tabs),
            "url": next((t.get("url", "") for t in itau_tabs), ""),
        }
    except Exception as exc:
        checks["itau_debug"] = {"status": "offline", "error": str(exc)}

    checks["pendentes"] = len(listar_pendentes())
    checks["armed"] = automation_armed
    checks["processando"] = processando
    return checks


@app.get("/status")
async def status():
    """Retorna o status atual do processador."""
    pendentes = listar_pendentes()
    return {
        "processando": processando,
        "armed": automation_armed,
        "pendentes": len(pendentes),
        "planilha": "contatos_whatsapp.xlsx",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
