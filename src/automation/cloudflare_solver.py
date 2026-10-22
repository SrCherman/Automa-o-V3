"""
cloudflare_solver.py - Aguarda a verificação normal do navegador, sem contornar desafios.
"""

import time
import logging

log = logging.getLogger("cloudflare_solver")

def check_and_solve_cloudflare(tab, max_wait_seconds: int = 12) -> bool:
    """
    Verifica se a aba atual está presa em uma TELA DE DESAFIO ativa do Cloudflare (Just a moment / Um momento / Executando verificação).
    Aguarda a resolução sem interferir com cliques sintéticos que invalidam o token do Turnstile.
    """
    try:
        start_time = time.time()
        while time.time() - start_time < max_wait_seconds:
            title = (tab.title or "").lower()
            html = (tab.html or "").lower()
            
            # Indicativos de bloqueio ativo do Cloudflare
            is_blocking = any(
                term in title for term in [
                    "um momento",
                    "just a moment",
                    "atenção",
                    "verificando",
                    "checking"
                ]
            ) or "executando verificação" in html or "#challenge-stage" in html or "cf-mitigation" in html

            if not is_blocking:
                return True

            log.info(f"[CLOUDFLARE] Bloqueio ativo detectado ('{tab.title}'). Aguardando validação do navegador...")
            time.sleep(1.5)

        log.warning("[CLOUDFLARE] Validação pendente. Intervenção do usuário pode ser necessária.")
        return False
    except Exception as exc:
        log.warning(f"[CLOUDFLARE] Erro ao verificar estado da aba: {exc}")
        return False
