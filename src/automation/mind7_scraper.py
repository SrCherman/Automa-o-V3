"""
mind7_scraper.py - Automação Híbrida Adaptativa do Portal Mind7 (Métodos de Coleta e Matching do MODELO BASE)
"""

import re
import time
import logging
from datetime import date, datetime
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse
from src.config import MIND7_URL, MIND7_CPF_URL, MIND7_LOGIN_URL, MIND7_USER, MIND7_PASS
from src.automation.mind7_rate_limit import (
    reserve_mind7_request, mark_mind7_blocked, Mind7AccessBlocked, ensure_mind7_allowed,
)
from src.ingestion.rules import calcular_score_nome, extrair_nome_real

log = logging.getLogger("mind7_scraper")

CHALLENGE_WAIT_SECONDS = 25.0
RESULT_GRACE_STEPS = 40  # 0,25 s cada = até 10 s de resposta atrasada
CHALLENGE_TEXTOS = (
    "verify you are human", "verifique se você é humano", "verifique se voce e humano",
    "checking your browser", "executando verificação", "executando verificacao",
    "enable javascript and cookies to continue",
)
CHALLENGE_ASSINATURAS = (
    "challenge-stage", "cf-mitigated", "cdn-cgi/challenge-platform",
)
BLOQUEIO_TEXTOS = (
    "too many requests", "rate limit exceeded", "access denied", "acesso negado",
    "acesso bloqueado", "excesso de consultas", "conta suspensa",
    "bloqueio por atividade", "bloqueio temporário", "bloqueio temporario",
)

class Mind7Unavailable(RuntimeError):
    """Sessão ou verificação do portal exige atenção antes de continuar."""


class Mind7GhostClick(Mind7Unavailable):
    """Não houve sinal observável de processamento após clicar; envio incerto."""


class Mind7Challenge(Mind7Unavailable):
    """Cloudflare desafiou o POST: a página precisa recarregar e o envio repetir."""


def normalizar_nascimento(value: str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        value = value.isoformat()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            nascimento = datetime.strptime(value or "", fmt).date()
            hoje = date.today()
            if date(hoje.year - 120, 1, 1) <= nascimento <= hoje:
                return nascimento.isoformat()
        except ValueError:
            pass
    return ""


def _valores_rotulados(texto: str, rotulo: str, valor: str) -> List[str]:
    """Valores após rótulo, na mesma linha (tab/dois-pontos) ou na linha seguinte."""
    padrao = rf"(?im)^[ \t]*{rotulo}[ \t]*:?[ \t]*(?:$[ \t]*\n+[ \t]*)?({valor})"
    return re.findall(padrao, texto)


def extrair_dados_cpf(texto: str, cpf: str) -> Dict[str, Any]:
    esperado = re.sub(r"\D", "", cpf)
    # O BigData inclui CPFs/nascimentos de parentes depois dos dados do titular.
    texto = re.split(
        r"(?im)^\s*[-=]*\s*(?:(?:MAPA|LISTA)\s+DE\s+)?"
        r"(?:PARENTES|POSS[ÍI]VEIS\s+VIZINHOS|VIZINHOS?|RELACIONAMENTOS)\b[^\n]*",
        texto, maxsplit=1,
    )[0]
    # O Mind7 às vezes elimina os zeros iniciais ao exibir CPFs.
    # Restaurar pelo comprimento; a confirmação exata continua obrigatória.
    valores_cpf = _valores_rotulados(
        texto, r"CPF", r"\d{3}\.\d{3}\.\d{3}-\d{2}|\d{8,11}")
    encontrados = set()
    for value in valores_cpf:
        digits = re.sub(r"\D", "", value)
        if len(digits) < 11 and esperado.startswith("0" * (11 - len(digits))):
            digits = digits.zfill(11)
        encontrados.add(digits)
    if encontrados != {esperado}:
        raise ValueError("Mind7 devolveu dados sem confirmação do CPF consultado")
    dados = {"cpf": esperado, "mind7_name": "", "birth_date": "", "cep": "", "house_number": ""}
    for nome in _valores_rotulados(texto, r"NOME(?:\s+COMPLETO)?(?!\s+D[AE]\b)", r"[^\n|]+"):
        nome = nome.strip()
        if nome and "COMPLETO" not in nome.upper():
            dados["mind7_name"] = nome
            break
    for nasc in _valores_rotulados(
            texto, r"(?:DATA\s+DE\s+)?NASC(?:IMENTO|\.)?", r"\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}"):
        dados["birth_date"] = normalizar_nascimento(nasc)
        if dados["birth_date"]:
            break
    linhas = [l.strip() for l in texto.splitlines() if re.search(r"\b\d{5}-?\d{3}\b", l)]
    # Linhas de CPF também têm 8 dígitos e não são endereço.
    linhas = [l for l in linhas if not re.search(r"(?i)\bcpf\b", l)]
    endereco = next((l for l in linhas if "receita federal" in l.lower()), linhas[0] if linhas else "")
    if endereco:
        dados["cep"] = re.search(r"\b(\d{5}-?\d{3})\b", endereco).group(1).replace("-", "")
        dados["house_number"] = extrair_numero_endereco(endereco)
    return dados

def _garantir_login_mind7(tab) -> bool:
    try:
        if urlparse(tab.url).hostname != urlparse(MIND7_LOGIN_URL).hostname:
            return False
        url_lower = tab.url.lower()
        if "acesso" in url_lower or "login" in url_lower:
            email_elem = tab.ele("@name=email", timeout=0.5) or tab.ele("@type=email", timeout=0.5) or tab.ele("#email", timeout=0.5)
            pass_elem = tab.ele("@name=senha", timeout=0.5) or tab.ele("@name=password", timeout=0.5) or tab.ele("@type=password", timeout=0.5) or tab.ele("#senha", timeout=0.5)

            try:
                if email_elem:
                    email_elem.input(MIND7_USER, clear=True)
                if pass_elem:
                    pass_elem.input(MIND7_PASS, clear=True)
            except Exception:
                pass

            if email_elem and pass_elem:
                botao = (tab.ele("text:Entrar no painel", timeout=0.3)
                         or tab.ele("text:Entrar", timeout=0.3)
                         or tab.ele("text:Acessar", timeout=0.3)
                         or tab.ele("@type=submit", timeout=0.3)
                         or tab.ele("tag:button", timeout=0.3))
                if botao:
                    tentativas = 0
                    while tentativas < 2:
                        token = ""
                        for _ in range(40):
                            token = tab.run_js(
                                "const i=document.querySelector('input[name=\"cf-turnstile-response\"]');"
                                "return i ? i.value : ''") or ""
                            if token:
                                break
                            _clicar_desafio(tab)
                            time.sleep(0.5)
                        try:
                            botao.click()
                        except Exception:
                            pass
                        for _ in range(20):
                            time.sleep(0.5)
                            if "acesso" not in tab.url.lower() and "login" not in tab.url.lower():
                                return True
                            _verificar_bloqueio_mind7(tab)
                        corpo = _corpo_visivel(tab).lower()
                        if "captcha" not in corpo:
                            break
                        tentativas += 1

            log.info("[MIND7] Login preenchido; aguardando a sessão do navegador...")
            return False

        return True
    except Exception as exc:
        log.warning(f"[MIND7] Aviso ao checar tela de login: {exc}")
        return False


def _pegar_aba(page, keyword: str, fallback_url: str):
    try:
        tab = page.get_tab()
        if tab:
            if keyword in tab.url.lower():
                return tab
            if tab.url in ("about:blank", "chrome://newtab", "edge://newtab", ""):
                tab.get(fallback_url)
                return tab
    except Exception:
        pass

    for tid in page.get_tabs():
        tab = tid if hasattr(tid, "url") else page.get_tab(tid)
        if keyword in tab.url.lower():
            return tab
    return page.new_tab(fallback_url)


def _corpo_visivel(tab) -> str:
    try:
        body = tab.ele("tag:body", timeout=0.3)
        # DrissionPage .text inclui elementos ocultos; .raw_text usa innerText.
        return (body.raw_text or "") if body else ""
    except Exception:
        return ""


def _assinatura_desafio(tab, titulo="", *, html=True) -> bool:
    """Sinais de página de desafio Cloudflare (título/HTML), não texto solto."""
    try:
        title = titulo or getattr(tab, "title", "")
    except Exception:
        title = titulo
    if isinstance(title, str) and any(m in title.lower() for m in ("just a moment", "um momento")):
        return True
    if not html:
        return False
    try:
        page_html = tab.html
    except Exception:
        page_html = ""
    return isinstance(page_html, str) and any(m in page_html.lower() for m in CHALLENGE_ASSINATURAS)


def _em_desafio(tab, texto: str, titulo="", *, html=True) -> bool:
    lower = (texto or "").lower()
    return (any(m in lower for m in CHALLENGE_TEXTOS)
            or _assinatura_desafio(tab, titulo, html=html))


def _clicar_desafio(tab) -> bool:
    """Turnstile gerenciado pode exigir um clique real no checkbox.

    A janela do widget fica num shadow root fechado; clicamos pelas coordenadas
    do host, medidas depois de centralizar o elemento na viewport.
    """
    try:
        try:
            tab.run_cdp("Page.bringToFront")
            tab.run_cdp("Emulation.setFocusEmulationEnabled", enabled=True)
        except Exception:
            pass
        rect = tab.run_js("""
            const inp = document.querySelector('input[name="cf-turnstile-response"]');
            if (!inp) return "";
            inp.scrollIntoView({block: 'center'});
            let el = inp.parentElement;
            while (el && el.getBoundingClientRect().width < 200) el = el.parentElement;
            if (!el) return "";
            const r = el.getBoundingClientRect();
            return [r.left, r.top, r.width, r.height].join(',');
        """)
        if not rect:
            return False
        x0, y0, w, h = (float(v) for v in rect.split(","))
        x, y = x0 + min(14.0, w / 2), y0 + h / 2
        tab.run_cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x - 25, y=y - 10)
        time.sleep(0.15)
        tab.run_cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        time.sleep(0.15)
        for kind in ("mousePressed", "mouseReleased"):
            tab.run_cdp("Input.dispatchMouseEvent", type=kind, x=x, y=y,
                        button="left", clickCount=1, pointerType="mouse")
            time.sleep(0.05)
        log.info("[CLOUDFLARE] Checkbox clicado.")
        return True
    except Exception as exc:
        log.info("[CLOUDFLARE] Não foi possível clicar no checkbox: %s", exc)
        return False


def _verificar_bloqueio_mind7(tab, texto="", *, titulo="", esperar=True, checar_html=True):
    """Reconhece bloqueios e desafios; espera o Cloudflare resolver antes de concluir.

    Desafio transitório não cria marcador persistente: gera Mind7Unavailable para
    o chamador reconsiderar. Só o bloqueio declarado pelo portal é persistente.
    """
    if not texto:
        texto = _corpo_visivel(tab)
    lower = texto.lower() if isinstance(texto, str) else ""
    blocked = (
        "bloqueio automático" in lower or "bloqueio automatico" in lower
        or "limite de consultas" in lower or "acesso temporariamente bloqueado" in lower
        or ("consultas em 5 min" in lower and "automação" in lower)
        or any(message in lower for message in BLOQUEIO_TEXTOS)
    )
    if blocked:
        mark_mind7_blocked()
        raise Mind7AccessBlocked("Mind7 informou bloqueio de consultas. Processamento suspenso.")
    if not _em_desafio(tab, texto, titulo, html=checar_html):
        return
    if not esperar:
        mark_mind7_blocked("Resposta HTTP exige validação no navegador")
        raise Mind7AccessBlocked("Mind7 exige validação manual; consulta HTTP suspensa.")
    log.info("[CLOUDFLARE] Desafio ativo; aguardando o navegador validar...")
    deadline = time.monotonic() + CHALLENGE_WAIT_SECONDS
    ultimo_clique = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.5)
        atual = _corpo_visivel(tab)
        if atual and not any(m in atual.lower() for m in CHALLENGE_TEXTOS):
            log.info("[CLOUDFLARE] Desafio resolvido.")
            return
        # Managed challenge não resolve só: clicar no checkbox a cada 3 s.
        if time.monotonic() - ultimo_clique >= 3.0:
            ultimo_clique = time.monotonic()
            _clicar_desafio(tab)
    raise Mind7Unavailable(
        "Cloudflare ainda validando o navegador; nova tentativa em instantes sem reenviar consulta.")


def _logar_se_preciso(tab) -> bool:
    """Sessão Mind7 expira após desafio; entrar de novo antes de consultar."""
    if "Sair" in (_corpo_visivel(tab) or ""):
        return True
    if tab.ele("#documento", timeout=0):
        return True
    _verificar_bloqueio_mind7(tab)
    tab.get(MIND7_LOGIN_URL)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        _verificar_bloqueio_mind7(tab)
        if tab.ele("@type=password", timeout=0.3):
            return _garantir_login_mind7(tab)
        if "Sair" in (_corpo_visivel(tab) or ""):
            return True
        time.sleep(0.5)
    return False


def probe_mind7_access(page, stop_event=None, tab=None) -> bool:
    """GET inofensivo: confirma sessão e formulário sem gastar consulta.

    True somente quando o formulário aparece e não há desafio/bloqueio.
    """
    tab = tab or _pegar_aba(page, "mind", MIND7_URL)
    if not _logar_se_preciso(tab):
        return False
    tab.get(MIND7_URL)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False
        _verificar_bloqueio_mind7(tab)
        if tab.ele("#documento", timeout=1):
            return True
        if "acesso" in tab.url.lower() or "login" in tab.url.lower():
            if _garantir_login_mind7(tab):
                return True
        time.sleep(0.5)
    return False


def _pacote_desafiado(pkt) -> bool:
    """POST de consulta respondido com desafio Cloudflare (403/429)."""
    try:
        if pkt.response.status not in (403, 429):
            return False
        return "consult" in (pkt.url or "").lower()
    except Exception:
        return False


def _aguardar_botao_livre(tab, seconds=20.0) -> bool:
    """Não clicar enquanto o envio anterior ainda está processando."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        botao = tab.ele("#botao-consultar", timeout=0)
        if botao and botao.states.is_enabled:
            return True
        time.sleep(0.5)
    return False


def enviar_consulta_mind7(tab, botao, stop_event=None, timeout=30):
    """Um clique; observa a resposta antes de liberar a próxima consulta.

    Clique possivelmente ignorado: atualiza a aba, mas NUNCA reenvia sozinho.
    """
    def send():
        _verificar_bloqueio_mind7(tab)
        listener = None
        try:
            candidate = getattr(tab, "listen", None)
            if candidate is not None:
                # Captura POSTs do navegador sem depender de um endpoint específico.
                candidate.start(targets=True, method="POST")
                listener = candidate
        except Exception as exc:
            log.warning("Monitor de rede indisponível; mantendo espera da interface: %s", exc)

        try:
            botao.click()
            tab.scroll.to_bottom()
            return _aguardar_resultado_mind7(
                tab, timeout, listener=listener, stop_event=stop_event)
        except Mind7GhostClick as exc:
            _verificar_bloqueio_mind7(tab)
            try:
                tab.refresh()
            except Exception as refresh_exc:
                log.warning("Falha ao atualizar aba Mind7: %s", refresh_exc)
            raise Mind7Unavailable(
                "Clique sem resposta aparente: aba atualizada. "
                "Envio incerto; confira o registro antes de nova tentativa.") from exc
        finally:
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    log.warning("Não foi possível encerrar monitor de rede Mind7.")
    return reserve_mind7_request(stop_event=stop_event, send=send)


def _reabrir_formulario(tab, url=None, seconds=25.0):
    """Recarrega a consulta e espera o desafio/Cloudflare liberar o formulário."""
    alvo = url or tab.url
    try:
        tab.get(alvo)
    except Exception:
        pass
    deadline = time.monotonic() + seconds
    voltar = False
    while time.monotonic() < deadline:
        _verificar_bloqueio_mind7(tab)
        campo = tab.ele("#documento", timeout=1)
        if campo:
            return campo
        if "acesso" in tab.url.lower() or "login" in tab.url.lower():
            if _garantir_login_mind7(tab):
                return tab.ele("#documento", timeout=5)
        if not voltar and alvo.rstrip("/") not in tab.url.rstrip("/"):
            # O desafio pode redirecionar ao painel após resolver.
            voltar = True
            try:
                tab.get(alvo)
            except Exception:
                pass
        time.sleep(0.5)
    return None


def _enviar_com_refresh(tab, campo, valor, stop_event=None, *, timeout=30, url=None):
    """Reenvio após desafio: recarrega, reautentica se preciso e repete."""
    for tentativa in (1, 2, 3):
        botao = tab.ele("#botao-consultar", timeout=2)
        if not botao:
            _verificar_bloqueio_mind7(tab)
            raise Mind7Unavailable("Botão de consulta do Mind7 indisponível")
        _verificar_bloqueio_mind7(tab)
        try:
            return enviar_consulta_mind7(tab, botao, stop_event, timeout=timeout)
        except Mind7Challenge:
            if tentativa == 3:
                raise
            time.sleep(1.5)
            if not _garantir_login_mind7(tab):
                raise Mind7Unavailable("Mind7 pediu login após o desafio")
            campo = _reabrir_formulario(tab, url)
            if not campo:
                raise Mind7Unavailable("Formulário Mind7 indisponível após o desafio")
            campo.input(valor, clear=True)
            time.sleep(0.3)
    return None


def _limpar_resultado_mind7(tab):
    tab.run_js('''
        const resultado = document.getElementById("conteudo-retorno");
        if (resultado) resultado.replaceChildren();
        const copiar = document.getElementById("copiar_dados");
        if (copiar) copiar.value = "";
    ''')


def _aguardar_resultado_mind7(tab, seconds: float, *, listener=None,
                              stop_event=None) -> Optional[str]:
    inicio = time.monotonic()
    deadline = inicio + seconds
    anterior = ""
    estavel_desde = inicio
    viu_atividade = False
    viu_post = False
    monitor_funcional = listener is not None
    while time.monotonic() < deadline:
        ensure_mind7_allowed(stop_event)
        botao = tab.ele("#botao-consultar", timeout=0)
        copiar = tab.ele("#copiar_dados", timeout=0)
        texto = (copiar.value or "").strip() if copiar else ""
        resultado = tab.ele("#conteudo-retorno", timeout=0)
        visivel_raw = (resultado.raw_text or "") if resultado and resultado.states.is_displayed else ""
        visivel = visivel_raw.lower()
        _verificar_bloqueio_mind7(tab, checar_html=False)
        _verificar_bloqueio_mind7(tab, visivel, checar_html=False)
        if monitor_funcional and not viu_post:
            try:
                pkt = listener.wait(timeout=0.01, raise_err=False)
            except Exception:
                # Erro de instrumentação não prova ausência de envio.
                monitor_funcional = False
                pkt = None
            if pkt:
                viu_post = True
                if _pacote_desafiado(pkt):
                    try:
                        tab.refresh()
                    except Exception:
                        pass
                    raise Mind7Challenge(
                        "Cloudflare desafiou o envio; página recarregada para nova tentativa.")
        sem_dados = any(msg in visivel for msg in (
            "não foi possível encontrar", "nao foi possivel encontrar", "nenhum registro encontrado",
        ))
        # Layout novo não tem #copiar_dados: o CPF aparece como rótulo isolado.
        dados = texto if re.search(r"\b(?:cpf|cnpj)\s*:", texto, re.I) else (
            visivel_raw if re.search(r"(?im)^\s*(?:cpf|cnpj)\b", visivel_raw) else "")
        habilitado = bool(botao and botao.states.is_enabled)
        if not habilitado or dados or sem_dados or viu_post:
            viu_atividade = True
        # Só aceitar dados estáveis, com o botão de consulta reativado.
        pronto = habilitado and (dados or sem_dados)
        atual = dados if dados else visivel if sem_dados else ""
        if not pronto or atual != anterior:
            anterior = atual if pronto else ""
            estavel_desde = time.monotonic()
        elif time.monotonic() - estavel_desde >= 1.0:
            return dados or None
        time.sleep(0.25)
    ensure_mind7_allowed(stop_event)
    if not viu_atividade and (not monitor_funcional or not viu_post):
        raise Mind7GhostClick(
            "Nenhum indicador de loading, resultado ou POST concluído após o clique.")
    # Portal às vezes responde atrasado: prorrogar antes de declarar envio incerto,
    # evitando reenviar enquanto a primeira consulta ainda pode concluir.
    for _ in range(RESULT_GRACE_STEPS):
        ensure_mind7_allowed(stop_event)
        copiar = tab.ele("#copiar_dados", timeout=0)
        texto = (copiar.value or "").strip() if copiar else ""
        resultado = tab.ele("#conteudo-retorno", timeout=0)
        visivel_raw = (resultado.raw_text or "") if resultado else ""
        # Layout novo não tem #copiar_dados: o CPF aparece como rótulo isolado.
        if re.search(r"\b(?:cpf|cnpj)\s*:", texto, re.I):
            dados, atual, origem = texto, texto, "copy"
        elif re.search(r"(?im)^\s*(?:cpf|cnpj)\b", visivel_raw):
            dados, atual, origem = visivel_raw, visivel_raw, "vis"
        else:
            dados = atual = ""
            origem = ""
        _verificar_bloqueio_mind7(tab, checar_html=False)
        if dados:
            time.sleep(0.5)
            if origem == "copy":
                copiar = tab.ele("#copiar_dados", timeout=0)
                novo = (copiar.value or "").strip() if copiar else ""
            else:
                resultado = tab.ele("#conteudo-retorno", timeout=0)
                novo = (resultado.raw_text or "") if resultado else ""
            if novo == atual:
                return dados
        time.sleep(0.25)
    raise TimeoutError(f"Mind7 não concluiu a consulta em {seconds:g}s; envio incerto")


def extrair_numero_endereco(bloco_str: str) -> str:
    """Extrai o número do imóvel a partir do texto do endereço, evitando estritamente CEPs, anos, quadras e lotes."""
    if not bloco_str:
        return ""

    # 1. Remover CEP e padrões de CEP do texto para não poluir a busca de número
    texto_sem_cep = re.sub(r"cep\s*:?\s*\d{5}-?\d{3}", "", bloco_str, flags=re.I)
    texto_sem_cep = re.sub(r"\b\d{5}-?\d{3}\b", "", texto_sem_cep)
    texto_sem_cep = re.sub(r"\b\d{8}\b", "", texto_sem_cep)

    # 2. Procurar padrões explícitos como ", 123", "Nº 123", "NUM 123", "N 123", "NR 123"
    m_exp = re.search(r"(?:,\s*|n[oº]?\s*[:.]?\s*|num\s*[:.]?\s*|nr\s*[:.]?\s*|numero\s*[:.]?\s*)(\d{1,5})\b", texto_sem_cep, re.I)
    if m_exp:
        num = m_exp.group(1)
        if not (1900 <= int(num) <= 2030): # evitar anos
            return num

    # 3. Procurar padrão Logradouro + número (ex: "Rua 10, 540" ou "Av Brasil 540")
    m_log = re.search(r"(?:rua|av|avenida|travessa|alameda|praca|praça|rodovia|estrada|reserva|condominio)\s+[^,]+,\s*(\d{1,5})\b", texto_sem_cep, re.I)
    if m_log:
        num = m_log.group(1)
        if not (1900 <= int(num) <= 2030):
            return num

    # 4. Procurar por Quadra/Lote (ex: Qd 10 Lt 20)
    m_qd = re.search(r"\b(qd|quadra)\s*[\w.-]+\s*(lt|lote)\s*[\w.-]+", texto_sem_cep, re.I)
    if m_qd:
        return m_qd.group(0).upper()

    return "S/N"

def _nome_da_linha(valor: str) -> str:
    """Nome vem do primeiro trecho útil; o resto do bloco é seção seguinte."""
    for linha in (valor or "").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("---"):
            return linha
    return ""


def _extrair_candidatos_mind7(texto: str) -> List[Dict[str, Any]]:
    """
    Algoritmo de Extração de Candidatos do MODELO BASE:
    Suporta formato em blocos (CPF:... NOME:...) e formato tabular separado por pipe (|).
    """
    linhas = texto.split("\n")
    cpfs_ja_vistos = set()
    candidatos = []

    blocos = []
    atual = []
    for line in linhas:
        if (line.strip().startswith("CPF:") or re.search(r"^\s*CPF:\s*\d", line)) and atual:
            blocos.append("\n".join(atual))
            atual = [line]
        else:
            atual.append(line)
    if atual:
        blocos.append("\n".join(atual))

    for bloco in blocos:
        cpf_match = re.search(r"CPF:\s*(\d{11})", bloco)
        if not cpf_match:
            cpf_match = re.search(r"CPF:\s*(\d{3}\.\d{3}\.\d{3}-\d{2})", bloco)
        if not cpf_match:
            cpf_match = re.search(r"CPF:\s*(\d{8,10})(?!\d)", bloco)
        if not cpf_match:
            continue

        cpf_limpo = re.sub(r"\D", "", cpf_match.group(1)).zfill(11)
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
        nome_raw = _nome_da_linha(nome_raw) or "Desconhecido"

        cep = ""
        cep_match = re.search(r"\b(\d{5}-?\d{3})\b", bloco)
        if not cep_match:
            cep_match = re.search(r"\b(\d{8})\b", bloco)
            if cep_match:
                cep_cand = cep_match.group(1)
                if not (1000000 <= int(cep_cand) <= 99999999):
                    cep_match = None
        if cep_match:
            cep = cep_match.group(1).replace("-", "")

        nasc_match = re.search(
            r"\b(?:data\s+de\s+)?nasc(?:imento|\.)?\s*:\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})",
            bloco, re.I,
        )
        birth_date = normalizar_nascimento(nasc_match.group(1)) if nasc_match else ""

        house_number = extrair_numero_endereco(bloco)

        if cpf_limpo not in cpfs_ja_vistos:
            candidatos.append({
                "cpf": cpf_limpo,
                "nome": nome_raw,
                "cep": cep,
                "house_number": house_number,
                "birth_date": birth_date
            })
            cpfs_ja_vistos.add(cpf_limpo)

    # Fallback tabular do MODELO BASE
    if not candidatos:
        for linha_txt in linhas:
            if "CPF:" in linha_txt and "|" in linha_txt:
                partes = linha_txt.split("|")
                cpf_raw = ""
                nome_raw = ""
                for idx, p in enumerate(partes):
                    if "CPF:" in p:
                        cpf_raw = p.replace("CPF:", "").strip()
                        if idx + 1 < len(partes):
                            nome_raw = partes[idx + 1]

                cpf_limpo = re.sub(r"\D", "", cpf_raw).zfill(11)
                nome_raw = _nome_da_linha(nome_raw)
                if len(cpf_limpo) == 11 and len(set(cpf_limpo)) > 1 and nome_raw:
                    cep = ""
                    cep_match = re.search(r"\b(\d{5}-?\d{3})\b", linha_txt)
                    if cep_match:
                        cep = cep_match.group(1).replace("-", "")

                    nasc_match = re.search(
                        r"\b(?:data\s+de\s+)?nasc(?:imento|\.)?\s*:\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})",
                        linha_txt, re.I,
                    )
                    birth_date = normalizar_nascimento(nasc_match.group(1)) if nasc_match else ""

                    house_number = ""
                    digitos = re.findall(r"\b(\d{1,5})\b", linha_txt)
                    for d in digitos:
                        if 1900 <= int(d) <= 2030 or len(d) == 5:
                            continue
                        house_number = d
                        break

                    if cpf_limpo not in cpfs_ja_vistos:
                        candidatos.append({
                            "cpf": cpf_limpo,
                            "nome": nome_raw,
                            "cep": cep,
                            "house_number": house_number,
                            "birth_date": birth_date
                        })
                        cpfs_ja_vistos.add(cpf_limpo)

    return candidatos

def consultar_mind7_celular(page, phone: str, clean_name: str, tab=None, stop_event=None) -> Optional[Dict[str, Any]]:
    ensure_mind7_allowed(stop_event)
    tab = tab or _pegar_aba(page, "mind", MIND7_LOGIN_URL)
    _verificar_bloqueio_mind7(tab)
    url_lower = tab.url.lower()

    if "acesso" in url_lower or "login" in url_lower:
        _garantir_login_mind7(tab)
        raise Mind7Unavailable("Faça o login no Mind7 e retome a etapa")

    return _consultar_mind7_celular_browser_com_recovery(page, phone, clean_name, tab=tab, stop_event=stop_event)

def _consultar_mind7_celular_browser_com_recovery(page, phone: str, clean_name: str, tentativa: int = 1, tab=None, stop_event=None) -> Optional[Dict[str, Any]]:
    ensure_mind7_allowed(stop_event)
    tab = tab or _pegar_aba(page, "mind", MIND7_URL)
    _verificar_bloqueio_mind7(tab)
    if not _logar_se_preciso(tab):
        raise Mind7Unavailable("Sessão Mind7 não autenticada")

    try:
        if "consultas/celular" not in tab.url:
            tab.get(MIND7_URL)
            time.sleep(0.5)

        if not _garantir_login_mind7(tab):
            raise Mind7Unavailable("Mind7 aguarda login ou validação no navegador")

        campo = tab.ele("#documento", timeout=4)
        if not campo:
            _verificar_bloqueio_mind7(tab)
            raise Mind7Unavailable("Formulário de telefone do Mind7 indisponível")

        _verificar_bloqueio_mind7(tab)
        if not _aguardar_botao_livre(tab):
            raise Mind7Unavailable("Botão Mind7 ocupado; consulta anterior ainda em curso")
        _limpar_resultado_mind7(tab)

        campo.input(phone, clear=True)
        time.sleep(0.3)

        texto_copiar = _enviar_com_refresh(tab, campo, phone, stop_event, url=MIND7_URL)
        log.info(f"[MIND7] Consulta concluída para {phone}.")
        if texto_copiar is None:
            return None

        candidatos = _extrair_candidatos_mind7(texto_copiar)
        if not candidatos:
            return None

        log.info(f"[MIND7] Telefone {phone} ('{clean_name}') ➔ {len(candidatos)} candidato(s) retornado(s):")
        matches = []
        for idx, cand in enumerate(candidatos, 1):
            score = calcular_score_nome(clean_name, cand["nome"])
            log.info(f"   ├─ [{idx}] {cand['nome']:35s} | CPF: {cand['cpf']} | Score: {score:.2f}")
            if score >= 0.80:
                matches.append({
                    "cpf": cand["cpf"],
                    "nome": cand["nome"],
                    "cep": cand.get("cep", ""),
                    "house_number": cand.get("house_number", ""),
                    "birth_date": cand.get("birth_date", ""),
                    "score": score
                })

        if matches:
            matches.sort(key=lambda x: x["score"], reverse=True)
            best = matches[0]
            log.info(f"   └─ ✅ MATCH ENCONTRADO: '{clean_name}' ~ '{best['nome']}' | CPF: {best['cpf']} (Score: {best['score']:.2f})")
            return best

        log.info(f"   └─ ❌ DIVERGÊNCIA: Nenhum dos {len(candidatos)} candidatos bateu com '{clean_name}' (threshold >= 0.80).")
        return None

    except Exception as exc:
        log.error(f"Erro na consulta Mind7 Browser ({phone}): {exc}")
        raise

def consultar_mind7_cpf(page, cpf: str, tab=None, stop_event=None) -> Optional[Dict[str, Any]]:
    """
    Enriquecimento por CPF no Mind7 utilizando o Algoritmo do MODELO BASE:
    Prioriza a linha da RECEITA FEDERAL para extrair CEP e Número.
    """
    ensure_mind7_allowed(stop_event)
    tab = tab or _pegar_aba(page, "mind", MIND7_CPF_URL)
    _verificar_bloqueio_mind7(tab)
    if not _logar_se_preciso(tab):
        raise Mind7Unavailable("Sessão Mind7 não autenticada")

    try:
        if "consultas/cpf" not in tab.url:
            tab.get(MIND7_CPF_URL)
            time.sleep(0.5)
        if not _garantir_login_mind7(tab):
            raise Mind7Unavailable("Mind7 aguarda login ou validação no navegador")

        campo = tab.ele("#documento", timeout=4)
        if not campo:
            _verificar_bloqueio_mind7(tab)
            # O portal às vezes abre o painel inicial no lugar da consulta por CPF.
            tab.get(MIND7_CPF_URL)
            if not _garantir_login_mind7(tab):
                raise Mind7Unavailable("Mind7 aguarda login ou validação no navegador")
            campo = tab.ele("#documento", timeout=4)
            if not campo:
                _verificar_bloqueio_mind7(tab)
                raise Mind7Unavailable("Formulário de CPF do Mind7 indisponível")

        _verificar_bloqueio_mind7(tab)
        if not _aguardar_botao_livre(tab):
            raise Mind7Unavailable("Botão Mind7 ocupado; consulta anterior ainda em curso")
        _limpar_resultado_mind7(tab)

        campo.input(cpf, clear=True)
        time.sleep(0.2)

        texto_copiar = _enviar_com_refresh(tab, campo, cpf, stop_event, timeout=25, url=MIND7_CPF_URL)
        if texto_copiar is None:
            return None

        return extrair_dados_cpf(texto_copiar, cpf)
    except Exception as exc:
        log.error(f"Erro no enriquecimento CPF Mind7 ({cpf}): {exc}")
        raise
