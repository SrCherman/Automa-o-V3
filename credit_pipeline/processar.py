"""
processar.py - Consulta Mind7 + Credline Itau a partir da planilha.

Uso: python processar.py

Requisitos:
    - 2 janelas do Edge abertas com --remote-debugging-port
      (Mind7 na 9223, Credline na 9224)
    - pip install DrissionPage openpyxl
"""

import os, re, time, unicodedata, random
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.automation.mind7_scraper import consultar_mind7_cpf as consulta_cpf_compartilhada, normalizar_nascimento, Mind7Unavailable, _verificar_bloqueio_mind7, _limpar_resultado_mind7, enviar_consulta_mind7
from src.automation.mind7_rate_limit import Mind7AccessBlocked, ensure_mind7_allowed
from src.automation.c6_scraper import consultar_c6_cpf, C6Unavailable
from src.config import C6_OPTIONS, C6_PLATE, C6_VEHICLE_VALUE
from datetime import datetime
from DrissionPage import ChromiumPage, ChromiumOptions
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment

# --- Config ---
PLANILHA = os.path.join(os.path.dirname(__file__), "contatos_whatsapp.xlsx")
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")

import logging
# --- Configuração de Logging robusta ---
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "processamento.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("processar")

# Sobrescrever print nativo para registrar automaticamente no log
_builtin_print = print
def print(*args, **kwargs):
    msg = " ".join(str(arg) for arg in args)
    log.info(msg)

# --- Estatísticas Globais de Processamento ---
ESTATISTICAS = {
    "mind7": {
        "total": 0,
        "coletados": 0,
        "nomes_invalidos": 0,
        "sem_dados": 0,
        "erros": 0
    },
    "credline": {
        "total": 0,
        "aprovados_nao_correntistas": 0,
        "aprovados_correntistas": 0,
        "reprovados": 0,
        "pendentes": 0,
        "erros": 0,
        "simulacoes": 0
    },
    "enriquecimento": {
        "total": 0,
        "sucessos": 0,
        "sem_dados": 0,
        "erros": 0
    }
}
CREDLINE_AUDIT_DIR = os.path.join(LOG_DIR, "credline_auditoria")
MIND7_PORT = int(os.getenv("MIND7_DEBUG_PORT", "9223"))
ITAU_PORT  = int(os.getenv("ITAU_DEBUG_PORT", "9224"))
EDGE_EXE   = os.getenv("EDGE_EXE", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
MIND7_URL  = os.getenv("MIND7_URL", "https://mind-7.org/acesso/")
MIND7_SEARCH_URL = "https://mind-7.org/painel/consultas/celular/"
MIND7_START_DELAY = int(os.getenv("MIND7_START_DELAY", "30"))
CREDLINE_URL = "https://credlineitau.com.br/new-simulator"
CREDLINE_START_DELAY = int(os.getenv("CREDLINE_START_DELAY", "45"))
CREDLINE_BETWEEN_MIN = float(os.getenv("CREDLINE_BETWEEN_MIN", "0.8"))
CREDLINE_BETWEEN_MAX = float(os.getenv("CREDLINE_BETWEEN_MAX", "1.5"))
CREDLINE_BEFORE_TYPE_MIN = float(os.getenv("CREDLINE_BEFORE_TYPE_MIN", "0.3"))
CREDLINE_BEFORE_TYPE_MAX = float(os.getenv("CREDLINE_BEFORE_TYPE_MAX", "0.8"))
CREDLINE_BEFORE_CLICK_MIN = float(os.getenv("CREDLINE_BEFORE_CLICK_MIN", "0.3"))
CREDLINE_BEFORE_CLICK_MAX = float(os.getenv("CREDLINE_BEFORE_CLICK_MAX", "0.8"))
CREDLINE_TYPE_MIN = float(os.getenv("CREDLINE_TYPE_MIN", "0.03"))
CREDLINE_TYPE_MAX = float(os.getenv("CREDLINE_TYPE_MAX", "0.08"))
CREDLINE_AFTER_CLICK = float(os.getenv("CREDLINE_AFTER_CLICK", "1.0"))
CREDLINE_POLL_INTERVAL = float(os.getenv("CREDLINE_POLL_INTERVAL", "0.3"))
CREDLINE_RESULT_CHECKS = int(os.getenv("CREDLINE_RESULT_CHECKS", "40"))

# --- Simulação Globais ---
PLACA_VEICULO = ""
VALOR_VEICULO = ""


# =========================================================================
#  UTILS
# =========================================================================

_EMOJI_RE = re.compile("[\U00010000-\U0001FFFF"
                        "\U00002600-\U000027BF"
                        "\U0000FE00-\U0000FE0F"
                        "\U0000200D]+", flags=re.UNICODE)

def limpar(nome: str) -> str:
    """Remove emojis, acentos, deixa so letras minusculas."""
    if not nome: return ""
    nome = _EMOJI_RE.sub("", nome)
    nome = unicodedata.normalize("NFKD", nome)
    nome = "".join(c for c in nome if not unicodedata.combining(c))
    nome = re.sub(r"[^a-zA-Z\s]", " ", nome)
    return " ".join(nome.lower().split())

def normalizar_texto(texto: str) -> str:
    """Normaliza texto de tela para comparacoes sem depender de acentos."""
    texto = str(texto or "").lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.split())

def _nome_arquivo_seguro(valor: str) -> str:
    return re.sub(r"[^0-9a-zA-Z_-]+", "_", str(valor or "")).strip("_") or "sem_identificador"

def _salvar_auditoria_credline(tab, cpf: str, decisao: str, texto_original: str, texto_normalizado: str, motivo: str):
    os.makedirs(CREDLINE_AUDIT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{stamp}_{_nome_arquivo_seguro(cpf)}_{_nome_arquivo_seguro(decisao)}"
    txt_path = os.path.join(CREDLINE_AUDIT_DIR, f"{base}.txt")
    png_path = os.path.join(CREDLINE_AUDIT_DIR, f"{base}.png")

    screenshot_status = "nao_gerado"
    try:
        tab.get_screenshot(path=png_path)
        screenshot_status = png_path
    except Exception as exc:
        screenshot_status = f"falhou: {exc}"

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"horario={datetime.now().isoformat(timespec='seconds')}\n")
        f.write(f"cpf={cpf}\n")
        f.write(f"decisao={decisao}\n")
        f.write(f"motivo={motivo}\n")
        f.write(f"url={getattr(tab, 'url', '')}\n")
        f.write(f"screenshot={screenshot_status}\n")
        f.write("\n--- texto_normalizado ---\n")
        f.write((texto_normalizado or "")[:5000])
        f.write("\n\n--- texto_original ---\n")
        f.write((texto_original or "")[:5000])

    print(f"    Credline auditoria: {txt_path}")

def _decidir_credline(texto_normalizado: str):
    if "nao correntista" in texto_normalizado and "ja e correntista" not in texto_normalizado:
        return True, "NAO_CORRENTISTA", "texto contem 'nao correntista'"

    correntistas_termos = [
        "cliente correntista",
        "ja e correntista",
        "ja possui conta",
    ]
    termo_correntista = next((t for t in correntistas_termos if t in texto_normalizado), None)
    if termo_correntista:
        return True, "CORRENTISTA", f"correntista detectado: {termo_correntista}"

    falhas_transitorias = [
        "indisponivel",
        "tente novamente",
        "erro ao consultar",
    ]
    termo = next((t for t in falhas_transitorias if t in texto_normalizado), None)
    if termo:
        return None, "PENDENTE_ITAU", f"falha transitoria detectada: {termo}"

    reprovacoes = [
        "nao temos condicoes aprovaveis",
        "sem condicoes aprovaveis",
        "condicoes nao aprovaveis",
        "reprovado",
        "nao aprovado",
        "sem ofertas",
        "nao existem",
        "cpf invalido",
    ]
    termo = next((t for t in reprovacoes if t in texto_normalizado), None)
    if termo:
        return False, "CONSULTADO", f"reprovacao detectada: {termo}"

    return None, "PENDENTE_ITAU", "resultado nao reconhecido"

_APELIDOS = {
    "ze": ["jose"], "duda": ["eduarda", "eduardo"], "gabi": ["gabriela", "gabriel"],
    "chico": ["francisco"], "beto": ["roberto", "alberto", "carlos alberto", "umberto"],
    "dani": ["daniel", "daniela", "daniele"], "cris": ["cristiano", "cristina", "cristiane"],
    "leo": ["leonardo", "leoncio"], "rafa": ["rafael", "rafaela"], "babi": ["barbara"],
    "tati": ["tatiana", "tatiane"], "vivi": ["viviane", "vivian"], "fe": ["fernando", "fernanda", "felipe"],
    "ju": ["juliana", "julio", "julia"], "gui": ["guilherme"], "kaka": ["carlos", "ricardo", "claudia"],
    "cadu": ["carlos eduardo"], "zeca": ["jose carlos"], "nando": ["fernando"],
    "nanda": ["fernanda"], "dudu": ["eduardo"], "bia": ["beatriz"],
    "carol": ["carolina", "caroline"], "lu": ["luciana", "luiz", "luiza", "lucas"],
    "manu": ["emanuelle", "emanuel", "manuela"], "mavi": ["maria vitoria"],
    "ma": ["maria", "marcos", "marcelo"], "marquinhos": ["marcos"], "carlinhos": ["carlos"],
    "joaozinho": ["joao"], "paulinho": ["paulo"], "paulinha": ["paula"],
    "luisinho": ["luis"], "juca": ["jose"], "tiao": ["sebastiao"], "chicao": ["francisco"],
    "tonho": ["antonio"], "toninho": ["antonio"], "cida": ["aparecida"],
    "cidinha": ["aparecida"], "lia": ["eliana"], "beth": ["elisabeth"],
    "nete": ["marinete", "antonieta"], "coca": ["socorro"], "tiago": ["thiago"],
    "filipe": ["felipe"], "ana": ["anna"], "josy": ["josilene", "josiane", "josiele", "josefa", "josete"],
    "josi": ["josilene", "josiane", "josiele", "josefa", "josete"]
}

# Common noise titles and corporate words in WhatsApp names
_NOISE_WORDS = {
    "dr", "dra", "sr", "sra", "adv", "advogado", "advogada", "eng", "engenheiro",
    "prof", "professor", "pastor", "pr", "bispo", "pai", "mae", "filho", "neto", "junior", "jr",
    "ltda", "me", "eireli", "sa", "s.a", "cooperativa", "auto", "pecas", "mecanica", "servicos",
    "consultoria", "rh", "transportes", "loja", "salao", "studio", "clinica", "online",
    "digitando", "agora", "ontem", "mensagens", "nao", "lidas", "visto", "ultimo",
    "whatsapp", "contato", "cliente", "financeiro", "atendimento"
}

# Common prepositions/conjunctions in Portuguese names to ignore
_PREPOSITIONS = {"de", "da", "do", "das", "dos", "e", "a", "o"}

def normalizar_fonetica(w: str) -> str:
    if not w:
        return ""
    w = w.lower()
    w = w.replace("y", "i")
    w = w.replace("z", "s")
    w = w.replace("w", "v")
    w = w.replace("ph", "f")
    w = w.replace("h", "")
    w = re.sub(r"(.)\1+", r"\1", w)
    return w

def extrair_nome_real(nome: str) -> str:
    """
    Remove partes de empresa ou marcações que vêm após delimitadores comuns.
    Ex: "Antonio - Sociis RH" -> "Antonio"
        "Carlos | Sociis" -> "Carlos"
        "Ana (Sociis)" -> "Ana"
    """
    if not nome:
        return ""
    # Remover parênteses e o conteúdo dentro se parecer empresa/ruído
    nome = re.sub(r"\s*\(.*?\)", "", nome)
    # Split por separadores comuns cercados por espaços ou no fim
    partes = re.split(r"\s+[-|/@]\s+|\s+[-|/@]$", nome)
    if partes:
        return partes[0].strip()
    return nome

def limpar_nome_inteligente(nome: str, remover_separadores: bool = True) -> list:
    """
    Normaliza o nome, remove emojis, acentos, pontuação,
    e filtra termos de ruído comuns.
    Retorna uma lista de tokens relevantes em minúsculas.
    """
    if not nome:
        return []
    
    if remover_separadores:
        nome = extrair_nome_real(nome)
        
    # Remover emojis
    nome = _EMOJI_RE.sub("", nome)
    
    # Normalizar acentos
    nome = unicodedata.normalize("NFKD", nome)
    nome = "".join(c for c in nome if not unicodedata.combining(c))
    
    # Substituir pontuação por espaço
    nome = re.sub(r"[^a-zA-Z\s]", " ", nome)
    
    # Tokenizar
    tokens = nome.lower().split()
    
    # Filtrar preposições e palavras de ruído
    tokens_filtrados = []
    for t in tokens:
        if t in _PREPOSITIONS:
            continue
        if t in _NOISE_WORDS:
            continue
        if len(t) < 2:  # Ignorar iniciais isoladas (ex: "J")
            continue
        tokens_filtrados.append(t)
        
    return tokens_filtrados

def levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
        
    return previous_row[-1]

def string_similarity(s1: str, s2: str) -> float:
    if not s1 or not s2:
        return 0.0
    distance = levenshtein_distance(s1, s2)
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    return 1.0 - (distance / max_len)

def remover_diminutivo(nome: str) -> str:
    if nome.endswith("inho") and len(nome) > 5:
        return nome[:-4] + "o"
    if nome.endswith("inha") and len(nome) > 5:
        return nome[:-4] + "a"
    return nome

def token_match_score(tw: str, tm: str) -> float:
    """Calcula a similaridade entre um token do whatsapp (tw) e um do mind7 (tm)."""
    # 1. Normalização fonética
    tw_norm = normalizar_fonetica(tw)
    tm_norm = normalizar_fonetica(tm)
    
    # 2. Match exato pós-normalização
    if tw_norm == tm_norm:
        return 1.0
        
    # 3. Match por similaridade de caracteres pós-normalização
    sim = string_similarity(tw_norm, tm_norm)
    if sim >= 0.82:
        return sim
        
    # 4. Match por diminutivo
    tw_sem_dim = normalizar_fonetica(remover_diminutivo(tw))
    tm_sem_dim = normalizar_fonetica(remover_diminutivo(tm))
    if tw_sem_dim != tw_norm or tm_sem_dim != tm_norm:
        sim_dim = string_similarity(tw_sem_dim, tm_sem_dim)
        if sim_dim >= 0.82:
            return sim_dim * 0.95
            
    # 5. Match por prefixo (ex: "leo" bate com "leonardo" como prefixo)
    if len(tw_norm) >= 3 and tm_norm.startswith(tw_norm):
        ratio = len(tw_norm) / len(tm_norm)
        if ratio >= 0.4:
            return 0.85
            
    # 6. Match por apelido mapeado
    if tw in _APELIDOS:
        for nome_real in _APELIDOS[tw]:
            for tr in nome_real.split():
                tr_norm = normalizar_fonetica(tr)
                if tr_norm == tm_norm or tm_norm.startswith(tr_norm) or string_similarity(tr_norm, tm_norm) >= 0.82:
                    return 0.90
                    
    return 0.0

def calcular_score_nome(nome_whats: str, nome_mind7: str) -> float:
    """
    Calcula um score de 0.0 a 1.0 de compatibilidade entre o nome do WhatsApp e o do Mind7.
    """
    tokens_w = limpar_nome_inteligente(nome_whats, remover_separadores=True)
    tokens_m = limpar_nome_inteligente(nome_mind7, remover_separadores=False)
    
    if not tokens_w or not tokens_m:
        return 0.0
        
    # Se o nome do WhatsApp tem apenas 1 token (ex: "Carlos" ou "Zeca")
    if len(tokens_w) == 1:
        tw = tokens_w[0]
        # Queremos ver se esse único token bate com o primeiro nome do Mind7
        best_first_name_score = token_match_score(tw, tokens_m[0])
        if best_first_name_score >= 0.82:
            return best_first_name_score
        # Também procura em outros tokens do Mind7 (caso o primeiro nome seja composto e o whats pegou o segundo)
        for tm in tokens_m[1:]:
            score = token_match_score(tw, tm)
            if score >= 0.85:
                return score * 0.90
        return 0.0

    # Se o nome do WhatsApp tem 2 ou mais tokens (ex: "Carlos Silva")
    # Precisamos garantir:
    # 1. O primeiro nome (ou apelido) bate com o primeiro nome (ou composto) do Mind7.
    # 2. Pelo menos um sobrenome do WhatsApp bate com um sobrenome do Mind7.
    
    first_w = tokens_w[0]
    surnames_w = tokens_w[1:]
    
    first_m = tokens_m[0]
    surnames_m = tokens_m[1:]
    
    # 1. Validar primeiro nome
    first_name_score = token_match_score(first_w, first_m)
    if first_name_score < 0.82 and len(tokens_m) > 1:
        first_name_score_alt = token_match_score(first_w, tokens_m[1])
        if first_name_score_alt >= 0.85:
            first_name_score = first_name_score_alt * 0.95
            
    if first_name_score < 0.80:
        return 0.0
        
    # 2. Validar sobrenomes
    best_surname_scores = []
    
    for sw in surnames_w:
        best_score_for_sw = 0.0
        for sm in surnames_m:
            score = token_match_score(sw, sm)
            if score > best_score_for_sw:
                best_score_for_sw = score
        if best_score_for_sw >= 0.80:
            best_surname_scores.append(best_score_for_sw)
            
    if not best_surname_scores:
        return 0.0
        
    avg_surname_score = sum(best_surname_scores) / len(best_surname_scores)
    final_score = (first_name_score * 0.4) + (avg_surname_score * 0.6)
    
    return final_score

def nomes_batem(nome_whats: str, nome_mind7: str, threshold: float = 0.80) -> bool:
    """Verifica se o nome bate usando calcular_score_nome."""
    score = calcular_score_nome(nome_whats, nome_mind7)
    return score >= threshold


# =========================================================================
#  CONEXAO COM EDGE
# =========================================================================

def conectar(porta: int, nome: str):
    co = ChromiumOptions()
    co.set_browser_path(EDGE_EXE)
    co.set_local_port(porta)
    try:
        page = ChromiumPage(co)
        print(f"  OK - {nome} conectado (porta {porta})")
        return page
    except Exception as e:
        print(f"  FALHA - {nome}: {e}")
        return None

def pegar_aba(page, keyword, url_fallback):
    try:
        tabs = [page.get_tab(tid) for tid in page.tab_ids]
    except Exception:
        tabs = []
    for tab in tabs:
        try:
            if keyword in (tab.url or "").lower():
                return tab
        except Exception:
            continue
    for tab in tabs:
        try:
            if "credline" in (tab.url or "").lower():
                tab.get(url_fallback)
                return tab
        except Exception:
            continue
    for tab in tabs:
        try:
            if (tab.url or "").lower() in ("about:blank", "chrome://newtab", "edge://newtab", ""):
                tab.get(url_fallback)
                return tab
        except Exception:
            continue
    return page.new_tab(url_fallback)

def porta_debug_online(porta: int) -> bool:
    try:
        import requests
        requests.get(f"http://127.0.0.1:{porta}/json", timeout=3)
        return True
    except Exception:
        return False

def abrir_edge_debug(porta: int, profile_dir: str, url: str, titulo: str):
    import subprocess

    os.makedirs(profile_dir, exist_ok=True)
    subprocess.Popen(
        [
            EDGE_EXE,
            f"--remote-debugging-port={porta}",
            f"--user-data-dir={profile_dir}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    print(f"  Abrindo Edge {titulo} na porta {porta}...")

def conectar_com_retry(porta: int, nome: str, profile_dir: str, url: str, tentativas: int = 3, espera: int = 5):
    page = conectar(porta, nome)
    if page:
        return page

    if not porta_debug_online(porta):
        abrir_edge_debug(porta, profile_dir, url, nome)

    for tentativa in range(1, tentativas + 1):
        time.sleep(espera)
        page = conectar(porta, nome)
        if page:
            return page
        print(f"  [!] {nome}: tentativa de reconexao {tentativa}/{tentativas} falhou.")

    return None


# =========================================================================
#  MIND7 - busca por telefone
# =========================================================================
#
# Estrutura da tabela do Mind7 (confirmada por diagnostico):
#   <table class="table table-striped">
#     <thead> <tr> <th>CPF/CNPJ</th> <th>NOME</th> <th>TEL/CEL</th> <th>CIDADE/UF</th> </tr> </thead>
#     <tbody>
#       <tr>
#         <td><a href="...">12345678900</a></td>      <- col 0: CPF (dentro de <a>)
#         <td class="text-bold-500">NOME COMPLETO</td> <- col 1: NOME
#         <td class="text-bold-500">(32) 999...</td>    <- col 2: TEL
#         <td class="text-bold-500">CIDADE - UF</td>   <- col 3: CIDADE
#       </tr>
#     </tbody>
#   </table>

# (variavel global removida - logica anti-stale agora usa limpeza de DOM)

def consultar_mind7(page, telefone: str, nome_whats: str):
    """
    Busca telefone no Mind7.
    Retorna {"cpf": "...", "nome": "..."} se nome bater, senao None.
    """
    ensure_mind7_allowed()
    tab = pegar_aba(page, "mind", MIND7_URL)
    _verificar_bloqueio_mind7(tab)

    try:
        # Garantir que estamos na pagina certa (sem recarregar se ja estiver)
        if "consultas/celular" not in tab.url:
            tab.get(MIND7_SEARCH_URL)
            time.sleep(2)

        # Limpar campo e digitar novo telefone
        campo = tab.ele("#documento", timeout=5)
        if not campo:
            _verificar_bloqueio_mind7(tab)
            raise Mind7Unavailable("Formulário de telefone Mind7 indisponível")

        _verificar_bloqueio_mind7(tab)

        campo.input(telefone, clear=True)

        _limpar_resultado_mind7(tab)

        # Uma submissão por consulta; a quota compartilhada controla o ritmo.
        botao = tab.ele("#botao-consultar", timeout=3)
        if botao:
            _verificar_bloqueio_mind7(tab)
            texto_copiar = enviar_consulta_mind7(tab, botao, timeout=180)
        else:
            _verificar_bloqueio_mind7(tab)
            raise Mind7Unavailable("Botão de consulta Mind7 indisponível")

        if texto_copiar is None:
            print("    Mind7: numero nao encontrado")
            return None

        # Coletar TODOS os candidatos lendo o textarea escondido (pega as caixas de resultado + tabela)
        linhas_texto = texto_copiar.split("\n")
        cpfs_ja_vistos = set()  # Evita duplicatas
        candidatos = []

        # Divide o texto_copiar em blocos de candidatos (cada bloco inicia com um padrão de CPF)
        blocos_txt = []
        bloco_atual = []
        for linha in linhas_texto:
            if (linha.strip().startswith("CPF:") or re.search(r"^\s*CPF:\s*\d", linha)) and bloco_atual:
                blocos_txt.append("\n".join(bloco_atual))
                bloco_atual = [linha]
            else:
                bloco_atual.append(linha)
        if bloco_atual:
            blocos_txt.append("\n".join(bloco_atual))

        def extrair_numero_endereco(bloco_str: str) -> str:
            lines = bloco_str.split("\n")
            for line in lines:
                if any(k in line.lower() for k in ["endereço", "endereco", "logradouro", "rua", "av", "avenida", "travessa"]):
                    m = re.search(r"(?:rua|av|avenida|travessa|alameda|praca|rodovia|logradouro|endereco).*?,\s*(\d+)\b", line, re.I)
                    if m:
                        return m.group(1)
                    m = re.search(r"(?:rua|av|avenida|travessa|alameda|praca|rodovia|logradouro|endereco).*?\s+(\d+)\b", line, re.I)
                    if m:
                        return m.group(1)
            for line in lines:
                if any(k in line.lower() for k in ["endereço", "endereco", "logradouro", "rua", "av", "avenida", "travessa", "bairro"]):
                    digitos = re.findall(r"\b(\d{1,5})\b", line)
                    for d in digitos:
                        if 1900 <= int(d) <= 2030 or len(d) == 5:
                            continue
                        return d
            return ""

        for bloco in blocos_txt:
            cpf_match = re.search(r"CPF:\s*(\d{11})", bloco)
            if not cpf_match:
                cpf_match = re.search(r"CPF:\s*(\d{3}\.\d{3}\.\d{3}-\d{2})", bloco)
            if not cpf_match:
                continue

            cpf_limpo = re.sub(r"\D", "", cpf_match.group(1))
            if len(cpf_limpo) != 11 or len(set(cpf_limpo)) == 1:
                continue

            if "|" in bloco:
                partes = [p.strip() for p in bloco.split("|")]
                cpf_idx = -1
                for idx, p in enumerate(partes):
                    if "CPF:" in p:
                        cpf_idx = idx
                        break
                if cpf_idx != -1 and cpf_idx + 1 < len(partes):
                    nome_raw = partes[cpf_idx + 1]
                else:
                    nome_raw = "Desconhecido"
            else:
                nome_match = re.search(r"NOME:\s*(.*)", bloco, re.I)
                nome_raw = nome_match.group(1).strip() if nome_match else "Desconhecido"

            cep = ""
            cep_match = re.search(r"\b(\d{5}-\d{3})\b", bloco)
            if not cep_match:
                cep_match = re.search(r"\b(\d{8})\b", bloco)
                # Validar se o numero de 8 digitos esta no range de CEPs validos (01000000-99999999)
                if cep_match:
                    cep_candidato = cep_match.group(1)
                    if not (1000000 <= int(cep_candidato) <= 99999999):
                        cep_match = None
            if cep_match:
                cep = cep_match.group(1).replace("-", "")

            nasc_match = re.search(
                r"\b(?:data\s+de\s+)?nasc(?:imento|\.)?\s*:\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})",
                bloco, re.I,
            )
            birth_date = normalizar_nascimento(nasc_match.group(1)) if nasc_match else ""

            numero = extrair_numero_endereco(bloco)

            if cpf_limpo not in cpfs_ja_vistos:
                candidatos.append({
                    "nome": nome_raw,
                    "cpf": cpf_limpo,
                    "cep": cep,
                    "numero": numero,
                    "birth_date": birth_date
                })
                cpfs_ja_vistos.add(cpf_limpo)

        # Fallback caso formato tabular do Mind7
        if not candidatos:
            for linha_txt in linhas_texto:
                if "CPF:" in linha_txt and "|" in linha_txt:
                    partes = linha_txt.split("|")
                    cpf_raw = ""
                    name_raw = ""
                    for idx, p in enumerate(partes):
                        if "CPF:" in p:
                            cpf_raw = p.replace("CPF:", "").strip()
                            if idx + 1 < len(partes):
                                name_raw = partes[idx+1].strip()

                    cpf_limpo = re.sub(r"\D", "", cpf_raw)
                    if len(cpf_limpo) == 11 and len(set(cpf_limpo)) > 1 and name_raw:
                        cep = ""
                        cep_match = re.search(r"\b(\d{5}-?\d{3})\b", linha_txt)
                        if cep_match:
                            cep = cep_match.group(1).replace("-", "")

                        nasc_match = re.search(
                            r"\b(?:data\s+de\s+)?nasc(?:imento|\.)?\s*:\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})",
                            linha_txt, re.I,
                        )
                        birth_date = normalizar_nascimento(nasc_match.group(1)) if nasc_match else ""

                        numero = ""
                        digitos = re.findall(r"\b(\d{1,5})\b", linha_txt)
                        for d in digitos:
                            if 1900 <= int(d) <= 2030 or len(d) == 5:
                                continue
                            numero = d
                            break

                        if cpf_limpo not in cpfs_ja_vistos:
                            candidatos.append({
                                "nome": name_raw,
                                "cpf": cpf_limpo,
                                "cep": cep,
                                "numero": numero,
                                "birth_date": birth_date
                            })
                            cpfs_ja_vistos.add(cpf_limpo)

        if not candidatos:
            print("    Mind7: tabela sem dados uteis")
            return None

        # Log de candidatos unicos encontrados
        print(f"    Mind7: {len(cpfs_ja_vistos)} CPF(s) unicos encontrados")

        # Mostrar todos os resultados
        print(f"    Mind7: {len(candidatos)} resultado(s):")
        for j, cand in enumerate(candidatos, 1):
            safe_nome = cand['nome'].encode('ascii', 'replace').decode()
            print(f"      {j}. {safe_nome} | CPF: {cand['cpf']}")

        # Verificar quais nomes batem com o WhatsApp usando a inteligência de scoring
        matches = []
        cpfs_vistos = set()
        for cand in candidatos:
            if cand["cpf"] in cpfs_vistos:
                continue
            score = calcular_score_nome(nome_whats, cand["nome"])
            if score >= 0.80:
                safe = cand['nome'].encode('ascii', 'replace').decode()
                print(f"    -> MATCH ENCONTRADO: '{nome_whats}' ~ '{safe}' (Score: {score:.2f})")
                matches.append({
                    "cpf": cand["cpf"],
                    "nome": cand["nome"],
                    "cep": cand.get("cep", ""),
                    "numero": cand.get("numero", ""),
                    "birth_date": cand.get("birth_date", ""),
                    "score": score
                })
                cpfs_vistos.add(cand["cpf"])

        if matches:
            # Ordenar candidatos pelo score de similaridade (maior primeiro)
            matches.sort(key=lambda x: x["score"], reverse=True)
            return matches

        print(f"    Mind7: nenhum dos {len(candidatos)} nomes bate com '{limpar(nome_whats)}'")
        return None

    except (Mind7AccessBlocked, Mind7Unavailable, TimeoutError):
        raise
    except Exception as e:
        print(f"    [!] Erro Mind7: {e}")
        raise Mind7Unavailable("Falha na consulta Mind7; confira antes de reenviar.") from e


def consultar_mind7_cpf(page, cpf: str):
    return consulta_cpf_compartilhada(page, cpf)


# =========================================================================
#  CREDLINE ITAU - busca por CPF
# =========================================================================
#
# Estrutura do Credline (confirmada por diagnostico):
#   - Campo CPF: id="ids-input-0" (pode variar: ids-input-4, mat-input-0)
#   - Botao continuar: id="continuar-button"
#   - Botao limpar: texto "excluir_outlineLimpar", class="clear-button"
#   - Resultados aparecem como texto no DOM: "não correntista", "correntista", etc.

def _achar_campo_cpf(tab):
    """Encontra o campo de CPF no Credline sem capturar o input de busca global."""
    # 1. Procurar por seletores específicos do campo de documento/CPF
    for sel in ["#ids-input-0", "input[data-cy*='document']", "input[data-cy*='cpf']", "input[idsmask*='000.000.000-00']", "#ids-input-4", "#ids-input-1"]:
        campo = tab.ele(sel, timeout=2)
        if campo and campo.states.is_displayed:
            # Garante que não é um campo readonly ou disabled da segunda tela
            if not campo.attr("readonly") and not campo.attr("disabled"):
                return campo

    # 2. Procurar por qualquer input com a classe 'ids-input' que seja visível
    for inp in tab.eles("tag:input"):
        cls = inp.attr("class") or ""
        if "ids-input" in cls and not "search" in cls:
            if inp.states.is_displayed and not inp.attr("readonly") and not inp.attr("disabled"):
                return inp

    # 3. Metodo 3: label exata "CPF"
    for label in tab.eles("tag:label"):
        label_text = normalizar_texto(label.text or "")
        if label_text == "cpf":
            for_id = label.attr("for")
            if for_id:
                campo = tab.ele(f"#{for_id}", timeout=1)
                if campo and campo.states.is_displayed:
                    return campo
            p = label.parent()
            for _ in range(3):
                if not p: break
                inp = p.ele("tag:input", timeout=0.5)
                if inp and "mat-input" not in (inp.attr("class") or "") and inp.states.is_displayed:
                    return inp
                p = p.parent()

    return None


def _digitar_cpf_legacy(tab, campo, cpf: str):
    """Digita CPF no campo simulando o teclado físico de forma visual e à prova de falhas."""
    try:
        # Encontra a label
        label = tab.ele(f"tag:label@@for={campo.attr('id')}", timeout=1)
        
        # Estratégia de foco em cascata
        focado = False
        if label:
            try:
                label.click()
                focado = True
            except Exception:
                try:
                    tab.run_js("arguments[0].click();", label)
                    focado = True
                except: pass
                
        if not focado:
            try:
                tab.run_js('arguments[0].focus();', campo)
            except Exception:
                pass # último recurso
        
        time.sleep(0.3)
        
        # Digita simulando o teclado letra por letra (MUITO VISIVEL)
        for char in cpf:
            tab.actions.type(char)
            time.sleep(random.uniform(0.05, 0.15))
        time.sleep(0.5)
        
        # Dispara eventos "blur"
        tab.run_js('document.body.click();')
        time.sleep(0.5)
        return True
    except Exception as e:
        print(f"    [!] Erro ao digitar CPF: {e}")
        return False


def _ler_valor_cpf(tab, campo) -> str:
    try:
        valor = campo.value or campo.attr("value") or ""
    except Exception:
        valor = ""

    if not valor:
        try:
            valor = tab.run_js("return arguments[0].value || '';", campo) or ""
        except Exception:
            valor = ""

    return re.sub(r"\D", "", str(valor))


def _setar_valor_cpf_angular(tab, campo, cpf: str) -> bool:
    """Fallback para Angular Material quando o input visual nao sincroniza."""
    try:
        tab.run_js(
            """
            const input = arguments[0];
            const value = arguments[1];
            const setter = Object.getOwnPropertyDescriptor(
              window.HTMLInputElement.prototype,
              'value'
            ).set;
            setter.call(input, value);
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            input.dispatchEvent(new Event('blur', { bubbles: true }));
            """,
            campo,
            cpf,
        )
        time.sleep(0.4)
        return _ler_valor_cpf(tab, campo) == cpf
    except Exception as e:
        print(f"    [!] Credline: fallback Angular falhou: {e}")
        return False


def _digitar_cpf(tab, campo, cpf: str):
    """Digita CPF usando input nativo DrissionPage com validação de máscara."""
    try:
        campo.input(cpf, clear=True)
        time.sleep(0.3)

        valor_digitado = _ler_valor_cpf(tab, campo)
        if valor_digitado == cpf:
            print(f"    Credline: CPF confirmado no input ({valor_digitado})")
            return True

        if _setar_valor_cpf_angular(tab, campo, cpf):
            print(f"    Credline: CPF confirmado via fallback Angular ({cpf})")
            return True

        print(f"    [!] Credline: digitando caractere a caractere...")
        for char in cpf:
            tab.actions.type(char)
            time.sleep(0.05)

        return True
    except Exception as e:
        print(f"    [!] Erro ao digitar CPF: {e}")
        return False


def consultar_credline(page, cpf: str, placa: str = "", valor_veiculo: str = ""):
    """
    Consulta CPF no Credline e grava evidencia local da resposta.
    Retorna True para NAO CORRENTISTA, False para rejeicao definitiva e None para falha/indefinido.
    """
    tab = pegar_aba(page, "new-simulator", CREDLINE_URL)

    try:
        try:
            tab.set.window.max()
            tab.set.window.show()
            page.set.tab_to_front(tab)
        except Exception:
            pass

        if "new-simulator" not in tab.url.lower():
            tab.get(CREDLINE_URL)
            time.sleep(1.5)

        limpar_btn = tab.ele("@class:clear-button", timeout=1) or tab.ele('text=Limpar', timeout=1)
        if limpar_btn and limpar_btn.states.is_displayed:
            try:
                limpar_btn.click()
            except Exception:
                tab.run_js('document.querySelector(".clear-button").click();')
            time.sleep(0.4)

        campo = _achar_campo_cpf(tab)
        if not campo:
            print("    [!] Credline: campo CPF nao encontrado, recarregando...")
            tab.get(CREDLINE_URL)
            time.sleep(2)
            campo = _achar_campo_cpf(tab)

        if not campo:
            print("    [!] Credline: impossivel encontrar campo CPF")
            _salvar_auditoria_credline(tab, cpf, "PENDENTE_ITAU", "", "", "campo CPF nao encontrado")
            return None

        time.sleep(random.uniform(0.5, 1.2))

        if not _digitar_cpf(tab, campo, cpf):
            print("    [!] Credline: falha ao digitar CPF")
            _salvar_auditoria_credline(tab, cpf, "PENDENTE_ITAU", "", "", "falha ao digitar/confirmar CPF no input")
            return None

        time.sleep(random.uniform(0.5, 1.5))

        botao = tab.ele("#continuar-button", timeout=3)
        if not botao:
            print("    [!] Credline: botao continuar nao encontrado")
            _salvar_auditoria_credline(tab, cpf, "PENDENTE_ITAU", "", "", "botao continuar nao encontrado")
            return None

        try:
            botao.hover()
            time.sleep(0.2)
            botao.click()
        except Exception:
            tab.run_js('document.getElementById("continuar-button").click();')
        time.sleep(CREDLINE_AFTER_CLICK)

        for _ in range(CREDLINE_RESULT_CHECKS):
            try:
                body = tab.ele("tag:body", timeout=1)
                texto_original = body.text if body else ""
                texto_normalizado = normalizar_texto(texto_original)

                if "carregando" in texto_normalizado or "aguarde" in texto_normalizado or "loading" in texto_normalizado:
                    time.sleep(CREDLINE_POLL_INTERVAL)
                    continue

                if not texto_normalizado or len(texto_normalizado) < 50:
                    time.sleep(CREDLINE_POLL_INTERVAL)
                    continue

                erro_vermelho = tab.ele("tag:mat-error", timeout=0) or tab.ele(".mat-error", timeout=0) or tab.ele(".text-danger", timeout=0)
                if erro_vermelho and erro_vermelho.text:
                    motivo = f"erro visual detectado: {erro_vermelho.text[:120]}"
                    print(f"    Credline: erro vermelho detectado: {erro_vermelho.text[:60]}")
                    _salvar_auditoria_credline(tab, cpf, "PENDENTE_ITAU", texto_original, texto_normalizado, motivo)
                    return None

                resultado, decisao, motivo = _decidir_credline(texto_normalizado)
                if motivo != "resultado nao reconhecido":
                    _salvar_auditoria_credline(tab, cpf, decisao, texto_original, texto_normalizado, motivo)
                    if resultado is True:
                        if placa and valor_veiculo:
                            print(f"    [!] Cliente {decisao} identificado. Dando início à simulação por placa...")
                            # Fechar modal informativo de pré-aprovado se aparecer
                            try:
                                modal_fechar = tab.ele('xpath://button[contains(., "fechar")]', timeout=2)
                                if modal_fechar:
                                    modal_fechar.click()
                                    print("    [!] Modal informativo de pré-aprovado fechado.")
                                    time.sleep(0.5)
                            except Exception:
                                pass
                            
                            # Executar a simulação por placa
                            valor_financiado = simular_placa_credline(tab, placa, valor_veiculo)
                            return {"status": decisao, "valor": valor_financiado}
                        else:
                            return {"status": decisao, "valor": None}
                    elif resultado is False:
                        return {"status": "REPROVADO", "valor": None}
            except Exception as e:
                print(f"    [!] Credline loop erro: {e}")
            time.sleep(CREDLINE_POLL_INTERVAL)

        print("    [!] Credline: resultado indefinido/timeout")
        try:
            body = tab.ele("tag:body", timeout=1)
            texto_original = body.text if body else ""
        except Exception:
            texto_original = ""
        _salvar_auditoria_credline(
            tab,
            cpf,
            "PENDENTE_ITAU",
            texto_original,
            normalizar_texto(texto_original),
            "timeout aguardando resultado reconhecivel",
        )
        return None

    except Exception as e:
        print(f"    [!] Erro Credline: {e}")
        try:
            body = tab.ele("tag:body", timeout=1)
            texto_original = body.text if body else ""
        except Exception:
            texto_original = ""
        _salvar_auditoria_credline(
            tab,
            cpf,
            "PENDENTE_ITAU",
            texto_original,
            normalizar_texto(texto_original),
            f"excecao: {e}",
        )
        return None


# =========================================================================
#  ETAPA 1 - COLETA MIND7
# =========================================================================

def etapa_mind7(wb, ws, page_m):
    """Percorre contatos pendentes e coleta CPF/Nome do Mind7."""
    print("\n" + "=" * 55)
    print("  ETAPA 1: COLETA DE CPFs NO MIND7")
    print("=" * 55)

    pendentes = []
    for row in ws.iter_rows(min_row=2, values_only=False):
        status = str(row[5].value or "").strip().upper()
        nome   = str(row[1].value or "").strip()
        tel    = str(row[2].value or "").strip()
        cpf_ja = str(row[3].value or "").strip()
        # So buscar no Mind7 se status vazio/PENDENTE e CPF ainda nao preenchido
        if nome and tel and status in ("", "PENDENTE") and not cpf_ja:
            pendentes.append({"row": row[0].row, "nome": nome, "tel": tel})

    if not pendentes:
        print("\n  Nenhum contato precisa de coleta Mind7.")
        return

    print(f"\n  {len(pendentes)} contato(s) para coletar\n")

    total = len(pendentes)
    coletados = 0
    nomes_invalidos = 0
    sem_dados = 0
    erros = 0

    for i, c in enumerate(pendentes, 1):
        row, nome, tel = c["row"], c["nome"], c["tel"]
        nome_limpo = limpar(nome)

        if not nome_limpo or len(nome_limpo) < 2:
            print(f"[{i}/{total}] {nome} ({tel}) -> nome invalido, pulando")
            ws.cell(row=row, column=6, value="SEM DADOS")
            ws.cell(row=row, column=7, value="Nome invalido")
            nomes_invalidos += 1
            continue

        print(f"[{i}/{total}] {nome} ({tel})")

        try:
            resultados = consultar_mind7(page_m, tel, nome)

            if not resultados:
                ws.cell(row=row, column=6, value="SEM DADOS")
                ws.cell(row=row, column=7, value="Mind7: sem dados")
                sem_dados += 1
            else:
                # Salvar o primeiro CPF que bate o nome na planilha
                primeiro = resultados[0]
                ws.cell(row=row, column=4, value=primeiro["cpf"])
                ws.cell(row=row, column=5, value=primeiro["nome"])
                ws.cell(row=row, column=6, value="CPF COLETADO")
                ws.cell(row=row, column=7, value=f"Mind7: {len(resultados)} match(es)")
                ws.cell(row=row, column=8, value=primeiro.get("cep", ""))
                ws.cell(row=row, column=9, value=primeiro.get("numero", ""))
                # Registrar somente o nascimento real presente no Mind7.
                # Nunca inferir uma data a partir da idade ou do ano isolado.
                birth_date = normalizar_nascimento(primeiro.get("birth_date", ""))
                if birth_date and not ws.cell(row=row, column=_coluna(ws, "Data de Nascimento")).value:
                    ws.cell(row=row, column=_coluna(ws, "Data de Nascimento"), value=birth_date)
                coletados += 1
        except (Mind7AccessBlocked, Mind7Unavailable, TimeoutError):
            _salvar(wb)
            raise
        except Exception as e:
            print(f"    [!] Erro na coleta Mind7 para {nome}: {e}")
            ws.cell(row=row, column=6, value="SEM DADOS")
            ws.cell(row=row, column=7, value=f"Erro interno: {e}")
            erros += 1

        if i % 5 == 0:
            _salvar(wb)

    _salvar(wb)
    
    # Preencher Estatísticas Globais
    ESTATISTICAS["mind7"]["total"] = total
    ESTATISTICAS["mind7"]["coletados"] = coletados
    ESTATISTICAS["mind7"]["nomes_invalidos"] = nomes_invalidos
    ESTATISTICAS["mind7"]["sem_dados"] = sem_dados
    ESTATISTICAS["mind7"]["erros"] = erros

    print(f"\n  Etapa 1 concluida: {coletados}/{total} CPFs coletados.\n")


# =========================================================================
#  CREDLINE ITAU - versao auditada
# =========================================================================

def _marcar_veiculo_usado(tab) -> bool:
    """
    Garante a marcação do checker/radio/checkbox/toggle de 'Veículo Usado' / 'Usado' no Credline.
    Suporta múltiplos formatos: Radio button, Checkbox, Switch/Toggle e componentes Angular IDS/Material.
    """
    try:
        marcado = tab.run_js("""
            const selectors = [
                'input[type="radio"]', 
                'input[type="checkbox"]', 
                'ids-radio', 
                'ids-checkbox', 
                'ids-toggle', 
                'ids-switch', 
                'mat-radio-button', 
                'mat-checkbox',
                'mat-slide-toggle'
            ];
            
            const elements = Array.from(document.querySelectorAll(selectors.join(',')));
            for (let el of elements) {
                const val = (el.value || '').toLowerCase();
                const id = (el.id || '').toLowerCase();
                const name = (el.name || '').toLowerCase();
                const fcn = (el.getAttribute('formcontrolname') || '').toLowerCase();
                const datacy = (el.getAttribute('data-cy') || '').toLowerCase();
                const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                const labelText = (el.closest('label')?.textContent || el.parentElement?.textContent || el.textContent || '').toLowerCase();

                const isUsado = [val, id, name, fcn, datacy, aria, labelText].some(txt => 
                    txt.includes('usad') || txt.includes('seminov') || txt === 'u' || txt.includes('used')
                );

                if (isUsado) {
                    const isChecked = el.checked || 
                                      el.getAttribute('checked') === 'true' || 
                                      el.classList.contains('ids-radio--checked') || 
                                      el.classList.contains('mat-radio-checked') ||
                                      el.classList.contains('mat-checkbox-checked') ||
                                      el.getAttribute('aria-checked') === 'true';
                    if (!isChecked) {
                        const target = el.querySelector('input') || el.querySelector('label') || el;
                        target.click();
                        target.dispatchEvent(new Event('change', { bubbles: true }));
                        target.dispatchEvent(new Event('input', { bubbles: true }));
                        return true;
                    }
                    return true;
                }
            }

            const textNodes = Array.from(document.querySelectorAll('label, span, p, div, button, a'));
            for (let el of textNodes) {
                const t = (el.textContent || '').trim().toLowerCase();
                if (t === 'usado' || t === 'veículo usado' || t === 'veiculo usado' || t === 'usados' || t === 'seminovo' || t.startsWith('usado ') || t.endsWith(' usado')) {
                    const parent = el.closest('label') || el.closest('.ids-radio') || el.closest('.mat-radio-button') || el.closest('.ids-checkbox') || el;
                    const inp = parent.querySelector('input');
                    if (inp && (inp.checked || inp.getAttribute('aria-checked') === 'true')) {
                        return true;
                    }
                    (inp || parent).click();
                    return true;
                }
            }

            return false;
        """)
        if marcado:
            print("    [CREDLINE] Opção/Checker 'Veículo Usado' marcada com sucesso.")
            return True

        seletores_dp = [
            'xpath://input[(@type="radio" or @type="checkbox") and (contains(@value, "USADO") or contains(@value, "usado") or contains(@value, "USED") or contains(@formcontrolname, "usad") or contains(@data-cy, "usad") or contains(@data-cy, "used"))]',
            'xpath://label[contains(translate(., "USADO", "usado"), "usado") or contains(translate(., "SEMINOVO", "seminovo"), "seminovo")]',
            'xpath://span[contains(translate(., "USADO", "usado"), "usado") or contains(translate(., "SEMINOVO", "seminovo"), "seminovo")]',
            'xpath://div[contains(@class, "radio") or contains(@class, "checkbox") or contains(@class, "ids") or contains(@class, "mat")][contains(translate(., "USADO", "usado"), "usado")]',
            '@data-cy=vehicle-condition-used',
            '@data-cy=used-vehicle',
            '@data-cy=used',
            'text=Veículo Usado',
            'text=Veiculo Usado',
            'text=Usado'
        ]
        for sel in seletores_dp:
            try:
                el = tab.ele(sel, timeout=0.2)
                if el and el.states.is_displayed:
                    el.click()
                    print(f"    [CREDLINE] Opção 'Veículo Usado' marcada via seletor: {sel}")
                    return True
            except Exception:
                continue

    except Exception:
        pass
    return False

def simular_placa_credline(tab, placa: str, valor_veiculo: str) -> str | bool:
    try:
        tab.scroll.to_bottom()
        time.sleep(0.5)
        _marcar_veiculo_usado(tab)
        
        # 1. Clicar na Tab "Placa"
        btn_placa = tab.ele('xpath://button[@role="tab" and contains(., "Placa")]', timeout=5)
        if not btn_placa:
            btn_placa = tab.ele('text=Placa', timeout=3)
        if btn_placa:
            btn_placa.click()
            print("    Tab Placa selecionada.")
        else:
            print("    [!] Não foi possível encontrar a aba Placa. Simulação pulada.")
            return "ERRO_SIMULACAO"  # NAO CORRENTISTA confirmado, mas simulação não realizada
            
        time.sleep(1.0)
        _marcar_veiculo_usado(tab)
        
        # 2. Inserir a Placa do Veículo
        input_placa = tab.ele('@data-cy=vehicle-identification', timeout=5)
        if not input_placa:
            input_placa = tab.ele('xpath://input[@data-cy="vehicle-identification"]', timeout=3)
        if not input_placa:
            input_placa = tab.ele('input[formcontrolname="vehicleIdentification"]', timeout=3)
        if input_placa:
            input_placa.input(placa, clear=True)
            print(f"    Placa preenchida: {placa}")
        else:
            print("    [!] Campo de placa não encontrado. Simulação pulada.")
            return "ERRO_SIMULACAO"  # NAO CORRENTISTA confirmado, mas simulação não realizada
            
        time.sleep(0.5)
        
        # 3. Clicar em "Buscar veículo"
        btn_buscar = tab.ele('xpath://button[contains(., "Buscar veículo")]', timeout=5)
        if btn_buscar:
            btn_buscar.click()
            print("    Botão Buscar veículo clicado.")
        else:
            tab.run_js("const btn = Array.from(document.querySelectorAll('button')).find(el => el.textContent.includes('Buscar veículo')); if(btn) btn.click();")
            print("    Botão Buscar veículo acionado via JS.")
            
        time.sleep(1.5)
        
        # 4. Selecionar o Veículo Radio Button
        radio_veiculo = tab.ele('xpath://input[@type="radio" and @formcontrolname="vehicle"]', timeout=10)
        if not radio_veiculo:
            radio_veiculo = tab.ele('input[type="radio"][formcontrolname="vehicle"]', timeout=5)
        if radio_veiculo:
            radio_veiculo.click()
            print("    Veículo retornado foi selecionado (Radio Button).")
        else:
            print("    [!] Não apareceu o rádio button do veículo após busca de placa. Simulação pulada.")
            return "ERRO_SIMULACAO"  # NAO CORRENTISTA confirmado, mas simulação não realizada
            
        time.sleep(0.5)
        _marcar_veiculo_usado(tab)
        
        # 5. Inserir o Valor do Veículo (Bypass de máscara de moeda via JS robusto)
        print(f"    Injetando valor do veículo: R$ {valor_veiculo}")
        # Sanitizar valor: remover caracteres perigosos para JS
        valor_sanitizado = re.sub(r"[^0-9.,]", "", valor_veiculo)
        tab.run_js("""
            const el = document.querySelector('input[data-cy="vehicle-value"]') || document.querySelector('input[formcontrolname="vehicleValue"]');
            if (el) {
                el.value = arguments[0];
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
        """, valor_sanitizado)
        
        time.sleep(0.5)
        _marcar_veiculo_usado(tab)
        
        # 6. Clicar em "Simular financiamento"
        btn_simular = tab.ele('@data-cy=create-simulation-button', timeout=5)
        if not btn_simular:
            btn_simular = tab.ele('xpath://button[@data-cy="create-simulation-button"]', timeout=3)
        if btn_simular:
            btn_simular.click()
            print("    Botão Simular financiamento clicado.")
        else:
            tab.run_js("const btn = document.querySelector('button[data-cy=\"create-simulation-button\"]'); if(btn) btn.click();")
            print("    Botão Simular financiamento acionado via JS.")
            
        # 7. Aguardar e extrair o "Valor Financiado Disponvel"
        print("    Aguardando carregamento da simulao de financiamento (at 15s)...")
        # Tentamos primeiro o XPath super-especfico associado ao label "Valor  financiar"
        xpath_financiado = 'xpath://p[contains(text(), "Valor  financiar") or contains(text(), "Valor a financiar")]/parent::div/following-sibling::div/p'
        val_el = tab.ele(xpath_financiado, timeout=15)
        
        # Caso o XPath no encontre, tentamos obter mltiplos data-cy e pegar o segundo (que  o valor a financiar)
        if not val_el:
            val_els = tab.eles('@data-cy=vehicle-value-description')
            if not val_els:
                val_els = tab.eles('xpath://p[@data-cy="vehicle-value-description"]')
            if val_els:
                val_el = val_els[1] if len(val_els) >= 2 else val_els[0]
                
        if val_el:
            valor_financiado = val_el.text.strip()
            print(f"    >>> SIMULAO CONCLUDA! Valor Financiado Disponvel: {valor_financiado} <<<")
            return valor_financiado
        else:
            print("    [!] Elemento do valor financiado no apareceu (timeout).")
            return "ERRO_SIMULACAO"
    except Exception as e:
        print(f"    [!] Erro durante simulação por placa: {e}")
        return "ERRO_SIMULACAO"


# =========================================================================
#  ETAPA 2 - CONSULTA CREDLINE
# =========================================================================

def etapa_credline(wb, ws, page_i):
    """Percorre a planilha e consulta no Credline todos os CPFs que precisam."""
    print("\n" + "=" * 55)
    print("  ETAPA 3: CONSULTA NO ITAU CREDLINE")
    print("=" * 55)

    pendentes_credline = []
    for row in ws.iter_rows(min_row=2, values_only=False):
        status = str(row[5].value or "").strip().upper()
        cpf    = str(row[3].value or "").strip()
        nome   = str(row[4].value or "").strip()
        # Coluna D tem CPF valido E coluna F NAO tem resultado final
        ja_consultado = status in ("CONSULTADO", "NAO CORRENTISTA", "CORRENTISTA", "SEM DADOS")
        enriquecido = ws.cell(row[0].row, _coluna(ws, "Status Enriquecimento")).value in ("CONCLUIDO", "INCOMPLETO", "SEM DADOS")
        if cpf and len(re.sub(r"\D", "", cpf)) == 11 and not ja_consultado and enriquecido:
            pendentes_credline.append({
                "row": row[0].row,
                "cpf": re.sub(r"\D", "", cpf),
                "nome": nome or "(sem nome)",
                "nome_whats": str(row[1].value or "").strip(),
            })

    if not pendentes_credline:
        print("\n  Nenhum CPF pendente de consulta no Credline.")
        return

    print(f"\n  {len(pendentes_credline)} CPF(s) para consultar\n")

    total = len(pendentes_credline)
    aprovados_nao_correntista = 0
    aprovados_correntista = 0
    reprovados = 0
    pendentes = 0
    erros = 0
    simulacoes = 0

    for i, c in enumerate(pendentes_credline, 1):
        row = c["row"]
        cpf = c["cpf"]
        nome = c["nome"]
        safe_nome = nome.encode('ascii', 'replace').decode()
        print(f"[{i}/{total}] CPF {cpf} | {safe_nome}")

        # Humanizacao: pausa entre consultas
        time.sleep(random.uniform(CREDLINE_BETWEEN_MIN, CREDLINE_BETWEEN_MAX))

        try:
            e_nao_correntista = consultar_credline(page_i, cpf, PLACA_VEICULO, VALOR_VEICULO)
        except Exception as exc:
            print(f"    [!] Credline desconectou/erro inesperado: {exc}")
            print("    [!] Tentando reconectar ao Edge do Credline...")
            erros += 1
            page_i = conectar_com_retry(
                ITAU_PORT,
                "Credline",
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "edge_profile_itau"),
                CREDLINE_URL,
                tentativas=4,
                espera=5,
            )
            if page_i:
                time.sleep(5)
                try:
                    e_nao_correntista = consultar_credline(page_i, cpf, PLACA_VEICULO, VALOR_VEICULO)
                except Exception as retry_exc:
                    print(f"    [!] Credline falhou apos reconexao: {retry_exc}")
                    e_nao_correntista = None
            else:
                e_nao_correntista = None

        if isinstance(e_nao_correntista, dict):
            status_itau = e_nao_correntista.get("status")
            valor_fin_raw = e_nao_correntista.get("valor")
            
            if status_itau in ("NAO_CORRENT_ISTA", "NAO_CORRENTISTA", "CORRENTISTA"):
                status_excel = "NAO CORRENTISTA" if status_itau in ("NAO_CORRENT_ISTA", "NAO_CORRENTISTA") else "CORRENTISTA"
                
                if valor_fin_raw == "ERRO_SIMULACAO":
                    valor_fin = ""
                    obs_txt = "Aprovado (Simulacao falhou)"
                else:
                    valor_fin = valor_fin_raw if valor_fin_raw else ""
                    obs_txt = "Aprovado"
                    
                print(f"    >>> {status_excel} ENCONTRADO <<< (Financiado: {valor_fin}) | {obs_txt}")
                ws.cell(row=row, column=6, value=status_excel)
                ws.cell(row=row, column=7, value=obs_txt)
                ws.cell(row=row, column=11, value=valor_fin)  # K - Valor Financiado
                
                cell = ws.cell(row=row, column=6)
                if status_excel == "NAO CORRENTISTA":
                    cell.fill = PatternFill("solid", fgColor="C6EFCE")
                    cell.font = Font(color="006100", bold=True)
                    aprovados_nao_correntista += 1
                else:  # CORRENTISTA
                    cell.fill = PatternFill("solid", fgColor="DDEBF7")  # Azul claro
                    cell.font = Font(color="002060", bold=True)  # Azul escuro
                    aprovados_correntista += 1
                cell.alignment = Alignment(horizontal="center")
                
                if valor_fin_raw and valor_fin_raw != "ERRO_SIMULACAO":
                    simulacoes += 1
            elif status_itau == "REPROVADO":
                print(f"    Credline: CPF {cpf} rejeitado.")
                ws.cell(row=row, column=6, value="CONSULTADO")
                ws.cell(row=row, column=7, value="Nao e NAO CORRENTISTA/CORRENTISTA")
                reprovados += 1
            else:
                print(f"    [!] Credline: CPF {cpf} com status inesperado: {status_itau}")
                ws.cell(row=row, column=6, value="PENDENTE ITAU")
                ws.cell(row=row, column=7, value="Credline sem resultado confiavel; tente novamente")
                pendentes += 1
        else:
            print(f"    [!] Credline: CPF {cpf} sem resultado confiavel.")
            ws.cell(row=row, column=6, value="PENDENTE ITAU")
            ws.cell(row=row, column=7, value="Credline sem resultado confiavel; tente novamente")
            pendentes += 1

        if i % 5 == 0:
            _salvar(wb)

    _salvar(wb)
    
    # Preencher Estatísticas Globais
    ESTATISTICAS["credline"]["total"] = total
    ESTATISTICAS["credline"]["aprovados_nao_correntistas"] = aprovados_nao_correntista
    ESTATISTICAS["credline"]["aprovados_correntistas"] = aprovados_correntista
    ESTATISTICAS["credline"]["reprovados"] = reprovados
    ESTATISTICAS["credline"]["pendentes"] = pendentes
    ESTATISTICAS["credline"]["erros"] = erros
    ESTATISTICAS["credline"]["simulacoes"] = simulacoes

    aprovados_total = aprovados_nao_correntista + aprovados_correntista
    print(f"\n  Etapa 2 concluida: {aprovados_total}/{total} Aprovados ({aprovados_nao_correntista} Nao Correntista, {aprovados_correntista} Correntista).\n")


def de_duplicar_planilha(wb, ws) -> int:
    """Verifica e exclui contatos duplicados (pelo mesmo CPF ou Telefone) da planilha."""
    vistos_cpf = set()
    vistos_tel = set()
    linhas_para_deletar = []
    
    for r in range(2, ws.max_row + 1):
        tel = str(ws.cell(row=r, column=3).value or "").strip()
        tel_limpo = re.sub(r"\D", "", tel)
        
        cpf = str(ws.cell(row=r, column=4).value or "").strip()
        cpf_limpo = re.sub(r"\D", "", cpf)
        
        tem_tel = len(tel_limpo) >= 8
        tem_cpf = len(cpf_limpo) == 11
        
        deletar = False
        if tem_cpf:
            if cpf_limpo in vistos_cpf:
                deletar = True
            else:
                vistos_cpf.add(cpf_limpo)
                
        if not deletar and tem_tel:
            if tel_limpo in vistos_tel:
                deletar = True
            else:
                vistos_tel.add(tel_limpo)
                
        if deletar:
            linhas_para_deletar.append(r)
            
    if linhas_para_deletar:
        print(f"  [De-duplicação] Removendo {len(linhas_para_deletar)} linha(s) duplicada(s) (mesmo CPF ou Telefone)...")
        for r in sorted(linhas_para_deletar, reverse=True):
            ws.delete_rows(r)
        _salvar(wb)
        print("  [De-duplicação] Planilha salva sem as duplicidades.")
    else:
        print("  [De-duplicação] Nenhum contato duplicado encontrado.")
        
    return len(linhas_para_deletar)


# =========================================================================
#  ETAPA 3 - ENRIQUECIMENTO DE DADOS VIA CPF NO MIND7
# =========================================================================

def _coluna(ws, title):
    for cell in ws[1]:
        if cell.value == title:
            return cell.column
    col = max(ws.max_column, 11) + 1
    ws.cell(1, col, title)
    return col


def etapa_enriquecimento(wb, ws, page_m):
    """Etapa 02 obrigatória por CPF, independente do resultado bancário."""
    cols = {name: _coluna(ws, name) for name in ("Data de Nascimento", "Status Enriquecimento", "Observacao Enriquecimento", "Tentativas Enriquecimento")}
    cache = {}
    for row in range(2, ws.max_row + 1):
        cpf = re.sub(r"\D", "", str(ws.cell(row, 4).value or ""))
        status = ws.cell(row, cols["Status Enriquecimento"]).value
        if cpf and status == "CONCLUIDO" and ws.cell(row, cols["Data de Nascimento"]).value:
            cache[cpf] = {"birth_date": ws.cell(row, cols["Data de Nascimento"]).value,
                          "cep": ws.cell(row, 8).value, "house_number": ws.cell(row, 9).value}
    for row in range(2, ws.max_row + 1):
        cpf = re.sub(r"\D", "", str(ws.cell(row, 4).value or ""))
        status = str(ws.cell(row, cols["Status Enriquecimento"]).value or "").strip().upper()
        bank_status = str(ws.cell(row, 6).value or "").strip().upper()
        birth_date = ws.cell(row, cols["Data de Nascimento"]).value
        attempts = int(ws.cell(row, cols["Tentativas Enriquecimento"]).value or 0)
        if (len(cpf) != 11 or bank_status == "SEM DADOS" or status == "SEM DADOS"
                or (status == "CONCLUIDO" and birth_date) or attempts >= 3):
            continue
        ESTATISTICAS["enriquecimento"]["total"] += 1
        try:
            ws.cell(row, cols["Tentativas Enriquecimento"], attempts + 1)
            ws.cell(row, cols["Status Enriquecimento"], "PROCESSANDO")
            _salvar(wb)
            result = cache[cpf] if cpf in cache else consultar_mind7_cpf(page_m, cpf)
            if result and result.get("birth_date"):
                cache[cpf] = result
            if result:
                if result.get("mind7_name") and not ws.cell(row, 5).value:
                    ws.cell(row, 5, result["mind7_name"])
                for key, col in (("birth_date", cols["Data de Nascimento"]), ("cep", 8), ("house_number", 9)):
                    if result.get(key) and not ws.cell(row, col).value:
                        ws.cell(row, col, result[key])
            ready = bool(ws.cell(row, cols["Data de Nascimento"]).value)
            ws.cell(row, cols["Status Enriquecimento"], "CONCLUIDO" if ready else ("INCOMPLETO" if result else "SEM DADOS"))
            ws.cell(row, cols["Observacao Enriquecimento"], "Consulta por CPF concluída" if ready else "Nascimento não encontrado; confira antes do C6")
            ESTATISTICAS["enriquecimento"]["sucessos" if ready else "sem_dados"] += 1
        except (Mind7Unavailable, Mind7AccessBlocked, TimeoutError) as exc:
            ws.cell(row, cols["Tentativas Enriquecimento"], attempts)
            ws.cell(row, cols["Status Enriquecimento"], "PENDENTE")
            ws.cell(row, cols["Observacao Enriquecimento"], str(exc))
            _salvar(wb)
            break
        except Exception as exc:
            ws.cell(row, cols["Status Enriquecimento"], "ERRO")
            ws.cell(row, cols["Observacao Enriquecimento"], str(exc)[:200])
            ESTATISTICAS["enriquecimento"]["erros"] += 1
        _salvar(wb)
    _salvar(wb)


def etapa_c6(wb, ws, page):
    cols = {name: _coluna(ws, name) for name in ("Data de Nascimento", "Status Enriquecimento", "Status C6", "Valor Financiado C6", "Observacao C6", "Tentativas C6")}
    for row in range(2, ws.max_row + 1):
        cpf = re.sub(r"\D", "", str(ws.cell(row, 4).value or ""))
        status = ws.cell(row, cols["Status C6"]).value
        if status == "AGUARDANDO RESULTADO":
            ws.cell(row, cols["Status C6"], "CONFERIR RESULTADO")
            _salvar(wb)
            continue
        if status not in (None, "", "PENDENTE", "ERRO", "AGUARDANDO DADOS") or len(cpf) != 11:
            continue
        if ws.cell(row, cols["Status Enriquecimento"]).value not in ("CONCLUIDO", "INCOMPLETO", "SEM DADOS"):
            continue
        birthday = ws.cell(row, cols["Data de Nascimento"]).value
        options = dict(C6_OPTIONS)
        if not birthday:
            ws.cell(row, cols["Status C6"], "AGUARDANDO DADOS")
            ws.cell(row, cols["Observacao C6"], "Informe a data de nascimento")
            _salvar(wb)
            continue
        attempts = int(ws.cell(row, cols["Tentativas C6"]).value or 0)
        if attempts >= 3:
            continue
        ws.cell(row, cols["Tentativas C6"], attempts + 1)
        def before_submit():
            ws.cell(row, cols["Status C6"], "AGUARDANDO RESULTADO")
            _salvar(wb)
        try:
            result = consultar_c6_cpf(page, cpf, str(ws.cell(row, 3).value or ""), birthday,
                                      C6_PLATE, C6_VEHICLE_VALUE, options, before_submit)
            ws.cell(row, cols["Status C6"], result["status"])
            ws.cell(row, cols["Valor Financiado C6"]).value = result.get("financed_value")
            ws.cell(row, cols["Observacao C6"], result.get("observation", ""))
        except Exception as exc:
            uncertain = ws.cell(row, cols["Status C6"]).value == "AGUARDANDO RESULTADO"
            ws.cell(row, cols["Status C6"], "CONFERIR RESULTADO" if uncertain else "ERRO")
            ws.cell(row, cols["Observacao C6"], str(exc)[:200])
            if isinstance(exc, C6Unavailable) and not uncertain:
                ws.cell(row, cols["Tentativas C6"], attempts)
            _salvar(wb)
            if isinstance(exc, C6Unavailable):
                break
        _salvar(wb)


# =========================================================================


# =========================================================================
#  MAIN
# =========================================================================

def main():
    import sys
    args = [a.lower() for a in sys.argv[1:]]
    
    somente_mind7      = "--etapa1" in args or "--mind7" in args
    somente_credline   = "--etapa2" in args or "--credline" in args
    somente_enriquecer = "--etapa3" in args or "--enriquecimento" in args
    somente_dedup      = "--dedup" in args or "--deduplicar" in args
    somente_c6 = "--c6" in args or "--etapa4" in args
    rodar_tudo = not any((somente_mind7, somente_credline, somente_enriquecer, somente_dedup, somente_c6))

    global PLACA_VEICULO, VALOR_VEICULO
    if rodar_tudo or somente_credline:
        print("\n" + "=" * 55)
        print("  CONFIGURAÇÃO DE SIMULAÇÃO ITAÚ CREDLINE")
        print("=" * 55)
        PLACA_VEICULO = input("Digite a PLACA do veículo para simulação (ex: IWD0H56): ").strip().upper()
        while not PLACA_VEICULO:
            PLACA_VEICULO = input("Placa não pode ser vazia. Digite a PLACA do veículo: ").strip().upper()
            
        VALOR_VEICULO = input("Digite o VALOR do veículo (ex: 120.000,00 ou 120000): ").strip()
        while not VALOR_VEICULO:
            VALOR_VEICULO = input("Valor não pode ser vazio. Digite o VALOR do veículo: ").strip()
        print("=" * 55 + "\n")

        # Salvar em simulacao_dados.json para consistência com o FastAPI
        try:
            import json
            with open(os.path.join(os.path.dirname(__file__), "simulacao_dados.json"), "w", encoding="utf-8") as f:
                json.dump({"placa": PLACA_VEICULO, "valor": VALOR_VEICULO}, f, indent=4)
        except Exception as e:
            print(f"    [!] Erro ao salvar simulacao_dados.json: {e}")

    if somente_dedup:
        print("\n" + "=" * 55)
        print("  ROTINA DE DE-DUPLICAÇÃO DE CONTATOS")
        print(f"  {datetime.now():%d/%m/%Y %H:%M}")
        print("=" * 55)
        if not os.path.exists(PLANILHA):
            print(f"Planilha nao encontrada: {PLANILHA}")
            return
        wb = load_workbook(PLANILHA)
        ws = wb.active
        de_duplicar_planilha(wb, ws)
        return

    print("=" * 55)
    if somente_mind7:
        print("  ETAPA 1: COLETA MIND7")
    elif somente_credline:
        print("  ETAPA 3: CONSULTA CREDLINE")
    elif somente_enriquecer:
        print("  ETAPA 2: ENRIQUECIMENTO DE DADOS VIA CPF")
    elif somente_c6:
        print("  ETAPA 4: CONSULTA C6 AUTO")
    else:
        print("  AUTOMACAO: TELEFONE + ENRIQUECIMENTO CPF + ITAU + C6")
    print(f"  {datetime.now():%d/%m/%Y %H:%M}")
    print("=" * 55)

    if not os.path.exists(PLANILHA):
        print(f"Planilha nao encontrada: {PLANILHA}")
        return

    wb = load_workbook(PLANILHA)
    ws = wb.active

    page_m = None
    page_i = None
    base_dir = os.path.dirname(os.path.dirname(__file__))

    if rodar_tudo or somente_mind7:
        page_m = conectar_com_retry(
            MIND7_PORT,
            "Mind7",
            os.path.join(base_dir, "edge_profile_mind7"),
            MIND7_URL,
        )
        if page_m:
            if MIND7_START_DELAY > 0:
                print(f"\n  Aguardando {MIND7_START_DELAY}s para iniciar a Etapa 1 no link Mind7 ({MIND7_URL})...")
                time.sleep(MIND7_START_DELAY)
            etapa_mind7(wb, ws, page_m)
        else:
            print("\n[!] Mind7 offline. Pulando etapa 1.")

    if rodar_tudo or somente_enriquecer or somente_credline or somente_c6:
        # Etapa 02: todos os CPFs antes das consultas bancárias
        wb = load_workbook(PLANILHA)
        ws = wb.active
        if not page_m:
            page_m = conectar_com_retry(
                MIND7_PORT,
                "Mind7",
                os.path.join(base_dir, "edge_profile_mind7"),
                MIND7_URL,
            )
        if page_m:
            etapa_enriquecimento(wb, ws, page_m)
        else:
            print("\n[!] Mind7 offline. Enriquecimento pendente; consultas bancárias aguardam a etapa 02.")

    if rodar_tudo or somente_credline:
        # Recarregar planilha para pegar CPFs recem-coletados
        wb = load_workbook(PLANILHA)
        ws = wb.active
        page_i = conectar_com_retry(
            ITAU_PORT,
            "Credline",
            os.path.join(base_dir, "edge_profile_itau"),
            CREDLINE_URL,
        )
        if page_i:
            print(f"\n  Aguardando Credline carregar completamente ({CREDLINE_START_DELAY}s)...")
            time.sleep(CREDLINE_START_DELAY)
            etapa_credline(wb, ws, page_i)
        else:
            print("\n[!] Credline offline. Pulando etapa 2.")
            print("    Abra o Credline e rode: python processar.py --etapa2")

    if rodar_tudo or somente_c6:
        from src.automation.browser_factory import get_campaign_browser_page
        page_c6 = get_campaign_browser_page(0, "c6")
        etapa_c6(wb, ws, page_c6)

    print(f"\n{'=' * 55}")
    print(f"  PRONTO! PROCESSAMENTO FINALIZADO COM SUCESSO.")
    print(f"{'=' * 55}")
    
    print("\n" + "┌" + "─" * 53 + "┐")
    print("│             RESUMO DAS ESTATÍSTICAS                 │")
    print("├" + "─" * 53 + "┤")
    
    if rodar_tudo or somente_mind7:
        print("│ ETAPA 1 - MIND7:                                    │")
        print(f"│   - Contatos analisados: {ESTATISTICAS['mind7']['total']:<26} │")
        print(f"│   - CPFs Coletados (Match): {ESTATISTICAS['mind7']['coletados']:<23} │")
        print(f"│   - Nomes Inválidos: {ESTATISTICAS['mind7']['nomes_invalidos']:<30} │")
        print(f"│   - Números sem dados: {ESTATISTICAS['mind7']['sem_dados']:<28} │")
        print(f"│   - Erros no processo: {ESTATISTICAS['mind7']['erros']:<28} │")
        print("├" + "─" * 53 + "┤")
        
    if rodar_tudo or somente_credline:
        print("│ ETAPA 3 - CREDLINE ITAÚ:                            │")
        print(f"│   - CPFs consultados: {ESTATISTICAS['credline']['total']:<29} │")
        print(f"│   - Aprovados (Não Correntistas): {ESTATISTICAS['credline']['aprovados_nao_correntistas']:<17} │")
        print(f"│   - Aprovados (Correntistas): {ESTATISTICAS['credline']['aprovados_correntistas']:<21} │")
        print(f"│   - Simulações Realizadas: {ESTATISTICAS['credline']['simulacoes']:<24} │")
        print(f"│   - Rejeitados / Outros: {ESTATISTICAS['credline']['reprovados']:<26} │")
        print(f"│   - Pendentes / Timeouts: {ESTATISTICAS['credline']['pendentes']:<25} │")
        print(f"│   - Erros / Quedas de Conexão: {ESTATISTICAS['credline']['erros']:<20} │")
        print("├" + "─" * 53 + "┤")
        
    if rodar_tudo or somente_enriquecer:
        print("│ ETAPA 2 - ENRIQUECIMENTO CPF:                       │")
        print(f"│   - CPFs a enriquecer: {ESTATISTICAS['enriquecimento']['total']:<28} │")
        print(f"│   - Enriquecidos com Sucesso: {ESTATISTICAS['enriquecimento']['sucessos']:<21} │")
        print(f"│   - CPFs sem dados: {ESTATISTICAS['enriquecimento']['sem_dados']:<31} │")
        print(f"│   - Erros no processo: {ESTATISTICAS['enriquecimento']['erros']:<28} │")
        print("└" + "─" * 53 + "┘")
    else:
        print("└" + "─" * 53 + "┘")
    print(f"  Logs detalhados salvos em: {LOG_FILE}\n")


def _salvar(wb):
    """Salva planilha com retry, backup atômico e proteção contra corrupção."""
    tmp = PLANILHA + ".tmp"
    for tentativa in range(12):
        try:
            wb.save(tmp)
            if os.path.exists(PLANILHA):
                os.replace(tmp, PLANILHA)
            else:
                os.rename(tmp, PLANILHA)
            return
        except PermissionError:
            delay = min(0.5 * (tentativa + 1), 5.0)
            print(f"  [!] Planilha bloqueada (tentativa {tentativa+1}/12, aguardando {delay:.1f}s)...")
            time.sleep(delay)
        except Exception as e:
            print(f"  [!] Erro ao salvar: {e}")
            break
    # Fallback: salvar como backup timestamped
    backup = PLANILHA + f".backup_{int(time.time())}"
    try:
        wb.save(backup)
        print(f"  [!] Salvo como backup de emergência: {backup}")
    except Exception as e:
        print(f"  [!] FALHA TOTAL ao salvar planilha! Erro: {e}")
    raise OSError("Planilha central não pôde ser salva; etapa interrompida")


if __name__ == "__main__":
    main()
