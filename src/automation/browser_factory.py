"""
browser_factory.py - Gerenciador de Instâncias do Navegador com Isolamento por Porta e Perfis por Campanha
"""

import os
from pathlib import Path
from DrissionPage import ChromiumPage, ChromiumOptions
from src.config import HEADLESS_DEFAULT, MIND7_DEBUG_PORT, ITAU_DEBUG_PORT

BASE_SESSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "browser_sessions"
BASE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

def create_campaign_browser_options(campaign_id: int, service_name: str = "mind7") -> ChromiumOptions:
    co = ChromiumOptions()
    
    edge_exe = os.getenv("EDGE_EXE", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    chrome_exe = os.getenv("CHROME_EXE", r"C:\Program Files\Google\Chrome\Application\chrome.exe")

    if os.path.exists(edge_exe):
        co.set_browser_path(edge_exe)
    elif os.path.exists(chrome_exe):
        co.set_browser_path(chrome_exe)

    # PORTAS EXCLUSIVAS POR CAMPANHA PARA EVITAR CONFLITOS DE NAVEGAÇÃO
    # Exemplo: Campanha 1 -> Mind7 9222, Itaú 9223 | Campanha 2 -> Mind7 9224, Itaú 9225
    offset = campaign_id * 2
    if service_name.lower() == "mind7":
        debug_port = 9220 + offset
    elif service_name.lower() == "itau":
        debug_port = 9221 + offset
    elif service_name.lower() == "c6":
        debug_port = 12000 + campaign_id
    else:
        raise ValueError(f"Serviço de navegador desconhecido: {service_name}")

    co.set_local_port(debug_port)
    
    # DIRETÓRIO DE PERFIL ISOLADO POR CAMPANHA
    session_dir = BASE_SESSIONS_DIR / f"campaign_{campaign_id}_{service_name}"
    session_dir.mkdir(parents=True, exist_ok=True)
    co.set_user_data_path(str(session_dir))

    if HEADLESS_DEFAULT:
        co.set_argument("--headless=new")
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-gpu")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument("--window-size=1920,1080")
    co.set_argument("--start-maximized")
    co.set_argument("--disable-infobars")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--no-first-run")
    co.set_argument("--no-default-browser-check")
    co.set_argument("--lang=pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7")

    return co

def get_campaign_browser_page(campaign_id: int, service_name: str = "mind7") -> ChromiumPage:
    """
    Inicia ou conecta a uma instância isolada do navegador em porta e perfil exclusivos para a campanha.
    """
    options = create_campaign_browser_options(campaign_id, service_name)
    try:
        return ChromiumPage(options)
    except Exception as exc:
        import logging
        import time
        logging.getLogger("browser_factory").warning(f"Primeira tentativa falhou (Campanha {campaign_id}/{service_name}): {exc}. Retentando em 2s...")
        time.sleep(2)
        return ChromiumPage(options)

def get_browser_page(port: int = None) -> ChromiumPage:
    return get_campaign_browser_page(campaign_id=1, service_name="mind7" if port == MIND7_DEBUG_PORT else "itau")
