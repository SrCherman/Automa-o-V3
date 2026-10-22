"""
logger.py - Formatação de Logs Organizada, Elegante e Estruturada para o CreditoBot Pro V2
"""

import sys
import logging

COLOR_RESET = "\033[0m"
COLOR_DIM = "\033[2m"
COLOR_BOLD = "\033[1m"
COLOR_CYAN = "\033[36m"
COLOR_GREEN = "\033[32m"
COLOR_YELLOW = "\033[33m"
COLOR_RED = "\033[31m"
COLOR_MAGENTA = "\033[35m"

class CleanFormatter(logging.Formatter):
    """
    Formatter de log visualmente organizado com timestamps limpos [HH:MM:SS],
    níveis coloridos e ícones contextuais por módulo.
    """
    def __init__(self, use_color: bool = True):
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        time_str = self.formatTime(record, "%H:%M:%S")
        level = record.levelname

        if self.use_color:
            if level == "INFO":
                lvl_str = f"{COLOR_CYAN}INFO {COLOR_RESET}"
            elif level == "WARNING":
                lvl_str = f"{COLOR_YELLOW}WARN {COLOR_RESET}"
            elif level == "ERROR":
                lvl_str = f"{COLOR_RED}ERR  {COLOR_RESET}"
            else:
                lvl_str = f"{COLOR_DIM}{level:5s}{COLOR_RESET}"
        else:
            lvl_str = f"{level:5s}"

        msg = record.getMessage()

        # Selecionar ícone proporcional ao contexto
        if "[ORCHESTRATOR]" in msg:
            icon = "⚙️ "
        elif "[MIND7]" in msg or "Mind7" in msg:
            icon = "🔍 "
        elif "[CREDLINE]" in msg or "Credline" in msg:
            icon = "💳 "
        elif "[ENRIQUECIMENTO]" in msg:
            icon = "🏡 "
        elif "[DEDUPLICADOR]" in msg:
            icon = "🛡️ "
        elif "[CLOUDFLARE]" in msg:
            icon = "🛡️ "
        else:
            icon = "📌 "

        if self.use_color:
            return f"{COLOR_DIM}[{time_str}]{COLOR_RESET} {lvl_str} {icon}{msg}"
        else:
            return f"[{time_str}] [{level:5s}] {icon}{msg}"

def setup_logging():
    """Configura o sistema de log global com formato organizado."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CleanFormatter(use_color=True))
    root_logger.addHandler(handler)

    # Suprimir ruídos verborrágicos de bibliotecas
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("DrissionPage").setLevel(logging.WARNING)

def print_banner(title: str, subtitle: str = ""):
    """Imprime um banner limpo e destacado no log."""
    line = "=" * 70
    print(f"\n{COLOR_CYAN}{line}{COLOR_RESET}")
    print(f"{COLOR_BOLD}{COLOR_CYAN}  {title}{COLOR_RESET}")
    if subtitle:
        print(f"{COLOR_DIM}  {subtitle}{COLOR_RESET}")
    print(f"{COLOR_CYAN}{line}{COLOR_RESET}\n", flush=True)
