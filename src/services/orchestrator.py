"""
orchestrator.py - Orquestrador Multi-Campanhas Simultâneas com Isolamento Total de Portas e Navegação
"""

import asyncio
import threading
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy import or_, and_, func

from src.database.connection import SessionLocal
from src.database.crud import get_dashboard_stats
from src.database.models import Contact, Campaign
from src.automation.browser_factory import get_campaign_browser_page
from src.automation.mind7_scraper import consultar_mind7_celular, consultar_mind7_cpf, Mind7Unavailable
from src.automation.c6_scraper import consultar_c6_cpf, C6Unavailable
from src.automation.credline_scraper import consultar_credline_cpf
from src.config import MIND7_URL, MIND7_CPF_URL, C6_PLATE, C6_VEHICLE_VALUE, C6_OPTIONS

log = logging.getLogger("orchestrator")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[Any] = []
        self._loop = None

    async def connect(self, websocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    def set_loop(self, loop):
        """Define o event loop do asyncio para broadcast de WebSocket."""
        self._loop = loop

    def broadcast_sync(self, message: dict):
        if not self._loop:
            return
        for connection in list(self.active_connections):
            try:
                asyncio.run_coroutine_threadsafe(connection.send_json(message), self._loop)
            except Exception:
                try:
                    self.active_connections.remove(connection)
                except ValueError:
                    pass

ws_manager = ConnectionManager()

class CampaignWorkerSession:
    STAGES = {
        "AUTO": ("MIND7", "ENRIQUECIMENTO", "CREDLINE", "C6"),
        "MIND7": ("MIND7",),
        "ENRIQUECIMENTO": ("ENRIQUECIMENTO",),
        "CREDLINE": ("ENRIQUECIMENTO", "CREDLINE"),
        "C6": ("ENRIQUECIMENTO", "C6"),
    }
    ENRICHED = ("CONCLUIDO", "INCOMPLETO", "SEM DADOS")

    def __init__(self, campaign_id, campaign_name, placa, valor, etapa_inicial="AUTO"):
        self.campaign_id = campaign_id
        self.campaign_name = campaign_name
        self.placa_veiculo = placa
        self.valor_veiculo = valor
        self.etapa_inicial = (etapa_inicial or "AUTO").upper()
        self.is_running = False
        self.is_armed = False
        self._thread = None
        self._stop = threading.Event()
        self._done = {stage: threading.Event() for stage in self.STAGES[self.etapa_inicial]}

    def start(self):
        self.is_running = self.is_armed = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.is_armed = False
        self._stop.set()

    def _recover(self):
        with SessionLocal() as db:
            for c in db.query(Contact).filter(Contact.campaign_id == self.campaign_id, or_(
                Contact.credline_status == "PROCESSANDO", Contact.enrichment_status == "PROCESSANDO",
                Contact.c6_status.in_(("PROCESSANDO", "AGUARDANDO RESULTADO"))
            )):
                if c.credline_status == "PROCESSANDO":
                    c.credline_status = "CPF COLETADO" if c.cpf else "PENDENTE"
                if c.enrichment_status == "PROCESSANDO":
                    c.enrichment_status = "PENDENTE"
                if c.c6_status == "PROCESSANDO":
                    c.c6_status = "PENDENTE"
                elif c.c6_status == "AGUARDANDO RESULTADO":
                    c.c6_status = "CONFERIR RESULTADO"
                    c.c6_observation = "Execução interrompida após envio. Confira o C6 antes de repetir."
            db.commit()

    def _run_loop(self):
        threads = []
        try:
            self._recover()
            mind7_page = None
            if any(s in self._done for s in ("MIND7", "ENRIQUECIMENTO")):
                try:
                    mind7_page = get_campaign_browser_page(self.campaign_id, "mind7")
                except Exception:
                    log.exception("Mind7 indisponível na campanha %s", self.campaign_id)
            for stage in self._done:
                thread = threading.Thread(target=self._run_stage, args=(stage, mind7_page), daemon=True)
                threads.append(thread)
                thread.start()
            for thread in threads:
                thread.join()
        finally:
            self.is_running = self.is_armed = False
            ws_manager.broadcast_sync({"type": "progress_update", "campaign_id": self.campaign_id})

    def _next_contact(self, db, stage):
        q = db.query(Contact).filter(Contact.campaign_id == self.campaign_id)
        has_cpf = and_(Contact.cpf.isnot(None), Contact.cpf != "")
        if stage == "MIND7":
            q = q.filter(or_(Contact.cpf.is_(None), Contact.cpf == ""),
                         func.coalesce(Contact.credline_status, "PENDENTE").in_(("PENDENTE", "")),
                         func.coalesce(Contact.mind7_attempts, 0) < 3)
        elif stage == "ENRIQUECIMENTO":
            q = q.filter(has_cpf, func.coalesce(Contact.enrichment_status, "PENDENTE").in_(("PENDENTE", "ERRO")),
                         func.coalesce(Contact.enrichment_attempts, 0) < 3)
        elif stage == "CREDLINE":
            q = q.filter(has_cpf, Contact.enrichment_status.in_(self.ENRICHED),
                         func.coalesce(Contact.credline_status, "PENDENTE").in_(("PENDENTE", "", "CPF COLETADO", "PENDENTE ITAU")))
        elif stage == "C6":
            ready = and_(Contact.birth_date.isnot(None), Contact.birth_date != "")
            q = q.filter(has_cpf, Contact.enrichment_status.in_(self.ENRICHED),
                         or_(func.coalesce(Contact.c6_status, "PENDENTE").in_(("PENDENTE", "ERRO")),
                             and_(Contact.c6_status == "AGUARDANDO DADOS", ready)),
                         func.coalesce(Contact.c6_attempts, 0) < 3)
        return q.order_by(Contact.id).first()

    def _upstream_running(self, stage):
        dependencies = {"MIND7": (), "ENRIQUECIMENTO": ("MIND7",),
                        "CREDLINE": ("ENRIQUECIMENTO",), "C6": ("ENRIQUECIMENTO",)}
        return any(s in self._done and not self._done[s].is_set() for s in dependencies[stage])

    def _run_stage(self, stage, mind7_page):
        page = tab = None
        try:
            if stage in ("MIND7", "ENRIQUECIMENTO"):
                page = mind7_page
                if page is None:
                    return
                url = MIND7_URL if stage == "MIND7" else MIND7_CPF_URL
                # Uma aba fixa por worker impede uma consulta de navegar sobre a outra.
                tab = next((t for t in page.get_tabs() if url in t.url), None) or page.new_tab(url)
            while self.is_armed:
                with SessionLocal() as db:
                    contact = self._next_contact(db, stage)
                    contact_id = contact.id if contact else None
                if contact_id is None:
                    if self._upstream_running(stage):
                        self._stop.wait(0.5)
                        continue
                    break
                if page is None:
                    page = get_campaign_browser_page(self.campaign_id, "itau" if stage == "CREDLINE" else "c6")
                if stage == "MIND7":
                    self._coletar_cpf_mind7_single(contact_id, page, tab)
                elif stage == "ENRIQUECIMENTO":
                    self._enriquecer_contato_single(contact_id, page, tab)
                elif stage == "CREDLINE":
                    self._simular_itau_single(contact_id, page)
                else:
                    self._simular_c6_single(contact_id, page)
                ws_manager.broadcast_sync({"type": "progress_update", "campaign_id": self.campaign_id})
                self._stop.wait(1)
        except Exception:
            log.exception("Etapa %s pausada na campanha %s; dados pendentes preservados", stage, self.campaign_id)
        finally:
            self._done[stage].set()

    def _coletar_cpf_mind7_single(self, contact_id, page, tab=None):
        with SessionLocal() as db:
            c = db.get(Contact, contact_id)
            if not c or c.cpf or not self.is_armed:
                return
            c.credline_status = "PROCESSANDO"
            c.mind7_attempts = (c.mind7_attempts or 0) + 1
            db.commit()
            try:
                result = consultar_mind7_celular(page, c.phone, c.clean_name, tab=tab)
                if result:
                    c.cpf, c.mind7_name = result["cpf"], result["nome"]
                    c.credline_status = "CPF COLETADO"
                    c.enrichment_status = "PENDENTE"
                    if c.c6_status == "AGUARDANDO CPF":
                        c.c6_status = "PENDENTE"
                    c.observation = "CPF e nome confirmados no Mind7"
                else:
                    c.credline_status = "SEM DADOS"
                    c.observation = "Mind7: sem registros ou nome divergente"
            except Exception as exc:
                c.credline_status = "PENDENTE" if c.mind7_attempts < 3 else "ERRO"
                c.observation = str(exc)[:200]
                db.commit()
                if isinstance(exc, Mind7Unavailable):
                    c.mind7_attempts -= 1
                    c.credline_status = "PENDENTE"
                    db.commit()
                    raise
                return
            db.commit()

    def _enriquecer_contato_single(self, contact_id, page, tab=None):
        with SessionLocal() as db:
            c = db.get(Contact, contact_id)
            if not c or not c.cpf or not self.is_armed or c.enrichment_status in (*self.ENRICHED, "PROCESSANDO"):
                return
            cached = db.query(Contact).filter(Contact.cpf == c.cpf, Contact.id != c.id,
                                               Contact.enrichment_status.in_(self.ENRICHED)).first()
            cached = {k: getattr(cached, k) for k in ("birth_date", "cep", "house_number")} if cached else None
            cpf, birth_date = c.cpf, c.birth_date
            attempts = (c.enrichment_attempts or 0) + 1
            values = {}
            current = db.query(Contact).filter(Contact.id == contact_id, Contact.cpf == cpf,
                                               Contact.birth_date == birth_date,
                                               Contact.enrichment_status == c.enrichment_status,
                                               func.coalesce(Contact.enrichment_attempts, 0) == attempts - 1)
            if not current.update({Contact.enrichment_status: "PROCESSANDO", Contact.enrichment_attempts: attempts}, synchronize_session=False):
                db.rollback()
                return
            db.commit()
            current = db.query(Contact).filter(Contact.id == contact_id, Contact.cpf == cpf,
                                               Contact.birth_date == birth_date,
                                               Contact.enrichment_status == "PROCESSANDO",
                                               Contact.enrichment_attempts == attempts)
            try:
                result = cached if cached is not None else consultar_mind7_cpf(page, cpf, tab=tab)
                if result:
                    for key in ("birth_date", "cep", "house_number"):
                        if result.get(key):
                            values[key] = func.coalesce(func.nullif(getattr(Contact, key), ""), result[key])
                has_birth_date = birth_date or (result or {}).get("birth_date")
                values["enrichment_status"] = "CONCLUIDO" if has_birth_date else ("INCOMPLETO" if result else "SEM DADOS")
                values["enrichment_observation"] = "Dados coletados por CPF" if has_birth_date else "Data de nascimento não encontrada; revisão necessária para o C6"
                values["enriched_at"] = datetime.now(timezone.utc)
                current.update(values, synchronize_session=False)
            except Exception as exc:
                values = {"enrichment_status": "PENDENTE" if isinstance(exc, Mind7Unavailable) else "ERRO",
                          "enrichment_observation": str(exc)[:200]}
                if isinstance(exc, Mind7Unavailable):
                    values["enrichment_attempts"] = attempts - 1
                current.update(values, synchronize_session=False)
                db.commit()
                if isinstance(exc, Mind7Unavailable):
                    raise
                return
            db.commit()

    def _simular_itau_single(self, contact_id, page):
        with SessionLocal() as db:
            c = db.get(Contact, contact_id)
            if not c or not self.is_armed or c.enrichment_status not in self.ENRICHED:
                return
            c.credline_status = "PROCESSANDO"
            db.commit()
            try:
                result = consultar_credline_cpf(page, c.cpf, self.placa_veiculo, self.valor_veiculo)
                c.credline_status = result.get("status", "PENDENTE ITAU")
                c.financed_value = result.get("financed_value")
                c.observation = result.get("observation", "")
                c.processed_at = datetime.now(timezone.utc)
                db.commit()
                if c.credline_status == "PENDENTE ITAU":
                    raise RuntimeError("Itaú pendente; retome a etapa após verificar o navegador")
            except Exception:
                if c.credline_status == "PROCESSANDO":
                    c.credline_status = "PENDENTE ITAU"
                    db.commit()
                raise

    def _simular_c6_single(self, contact_id, page):
        with SessionLocal() as db:
            c = db.get(Contact, contact_id)
            if not c or not self.is_armed or c.c6_status not in (None, "PENDENTE", "ERRO", "AGUARDANDO DADOS"):
                return
            if not c.birth_date:
                c.c6_status = "AGUARDANDO DADOS"
                c.c6_observation = "Informe a data de nascimento antes de consultar"
                db.commit()
                return
            cpf, phone, birth_date = c.cpf, c.phone, c.birth_date
            attempts = (c.c6_attempts or 0) + 1
            identity = db.query(Contact).filter(Contact.id == contact_id, Contact.cpf == cpf,
                                                Contact.phone == phone, Contact.birth_date == birth_date)
            if not identity.filter(Contact.c6_status == c.c6_status,
                                   func.coalesce(Contact.c6_attempts, 0) == attempts - 1).update(
                    {Contact.c6_status: "PROCESSANDO", Contact.c6_attempts: attempts}, synchronize_session=False):
                db.rollback()
                return
            db.commit()
            current = identity.filter(Contact.c6_attempts == attempts)
            submitted = False

            def before_submit():
                nonlocal submitted
                if not self.is_armed:
                    raise RuntimeError("Consulta interrompida antes do envio")
                # Gravar antes do clique impede reenvio automático após uma interrupção.
                if not current.filter(Contact.c6_status == "PROCESSANDO").update(
                        {Contact.c6_status: "AGUARDANDO RESULTADO"}, synchronize_session=False):
                    db.rollback()
                    raise RuntimeError("Identidade ou estado do contato alterado antes do envio ao C6")
                db.commit()
                submitted = True

            try:
                result = consultar_c6_cpf(page, cpf, phone, birth_date,
                                         C6_PLATE, C6_VEHICLE_VALUE, C6_OPTIONS, before_submit)
                current.filter(Contact.c6_status == ("AGUARDANDO RESULTADO" if submitted else "PROCESSANDO")).update(
                    {Contact.c6_status: result["status"], Contact.c6_financed_value: result.get("financed_value"),
                     Contact.c6_observation: result.get("observation", ""), Contact.c6_processed_at: datetime.now(timezone.utc)},
                    synchronize_session=False)
            except Exception as exc:
                current.filter(Contact.c6_status == ("AGUARDANDO RESULTADO" if submitted else "PROCESSANDO")).update(
                    {Contact.c6_status: "CONFERIR RESULTADO" if submitted else "ERRO", Contact.c6_observation: str(exc)[:200]},
                    synchronize_session=False)
                db.commit()
                if isinstance(exc, C6Unavailable):
                    raise
                return
            db.commit()

class MultiCampaignOrchestrator:
    def __init__(self):
        self.active_sessions: Dict[int, CampaignWorkerSession] = {}
        self._lock = threading.Lock()

    def get_state(self, target_campaign_id: Optional[int] = None) -> dict:
        db = SessionLocal()
        try:
            stats = get_dashboard_stats(db, target_campaign_id)
            running_list = [
                {
                    "campaign_id": sess.campaign_id,
                    "campaign_name": sess.campaign_name,
                    "placa_veiculo": sess.placa_veiculo,
                    "valor_veiculo": sess.valor_veiculo,
                    "etapa_inicial": sess.etapa_inicial,
                    "is_running": sess.is_running
                }
                for sess in self.active_sessions.values() if sess.is_running
            ]

            return {
                "is_running": len(running_list) > 0,
                "running_campaigns_count": len(running_list),
                "running_campaigns": running_list,
                "stats": stats
            }
        finally:
            db.close()

    def start_campaign(self, campaign_id: int, placa: str = "IWD0H56", valor: str = "120.000,00", etapa_inicial: str = "AUTO") -> dict:
        if etapa_inicial.upper() not in CampaignWorkerSession.STAGES:
            return {"status": "error", "message": "Etapa desconhecida"}
        with self._lock:
            existing = self.active_sessions.get(campaign_id)
            if existing and existing.is_running:
                return {"status": "busy", "message": f"A campanha {campaign_id} já está rodando em segundo plano."}

            db = SessionLocal()
            try:
                camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
                camp_name = camp.name if camp else f"Campanha #{campaign_id}"
            finally:
                db.close()

            session = CampaignWorkerSession(campaign_id, camp_name, placa, valor, etapa_inicial)
            self.active_sessions[campaign_id] = session
            session.start()

            nomes_etapas = {
                "AUTO": "Modo Automático (Todas as Etapas)",
                "MIND7": "Etapa 1 - Coleta de CPF (Mind7)",
                "CREDLINE": "Etapa 3 - Simulação de Crédito (Itaú Credline)",
                "ENRIQUECIMENTO": "Etapa 2 - Enriquecimento por CPF (Mind7)",
                "C6": "Etapa 4 - Consulta C6 Auto"
            }
            etapa_label = nomes_etapas.get(etapa_inicial.upper(), etapa_inicial)

            return {
                "status": "started",
                "campaign_id": campaign_id,
                "campaign_name": camp_name,
                "etapa_inicial": etapa_inicial.upper(),
                "message": f"Campanha '{camp_name}' iniciada em [{etapa_label}] com sucesso."
            }

    def stop_campaign(self, campaign_id: Optional[int] = None) -> dict:
        with self._lock:
            if campaign_id:
                session = self.active_sessions.get(campaign_id)
                if session:
                    session.stop()
                    return {"status": "stopped", "message": f"Interrupção solicitada para a campanha {campaign_id}."}
                return {"status": "not_found", "message": "Campanha não está em execução."}
            else:
                for session in self.active_sessions.values():
                    session.stop()
                return {"status": "stopped", "message": "Interrupção solicitada para TODAS as campanhas."}

orchestrator = MultiCampaignOrchestrator()
