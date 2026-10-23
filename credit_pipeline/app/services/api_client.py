import requests
import json
import re
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.automation.mind7_rate_limit import reserve_mind7_request, mark_mind7_blocked, Mind7AccessBlocked
from src.automation.mind7_scraper import _verificar_bloqueio_mind7, Mind7Unavailable
from bs4 import BeautifulSoup
from DrissionPage import ChromiumPage, ChromiumOptions
from app.utils.formatters import comparar_primeiro_nome

class DirectAPIClient:
    def __init__(self):
        self.session = requests.Session()
        self.mind7_browser_port = int(os.getenv("MIND7_DEBUG_PORT", "9223"))
        self.itau_browser_port = int(os.getenv("ITAU_DEBUG_PORT", "9224"))
        
    def _obter_auth_do_navegador(self, mind7_port: int | None = None, itau_port: int | None = None):
        """
        Conecta ao Edge aberto e extrai os cookies e tokens de autorização atuais.
        """
        mind7_port = int(mind7_port or self.mind7_browser_port)
        itau_port = int(itau_port or self.itau_browser_port)
        browser_path = os.getenv("EDGE_EXE", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
        
        auth_data = {
            "mind7_cookies": "",
            "itau_headers": {}
        }
        
        # Extraindo Cookies do Mind7
        try:
            co_mind = ChromiumOptions().set_local_port(mind7_port)
            co_mind.set_browser_path(browser_path)
            page_mind = ChromiumPage(co_mind)
            
            tab_mind = None
            for tab_id in page_mind.get_tabs():
                t = page_mind.get_tab(tab_id)
                if "mind-7.org" in t.url:
                    tab_mind = t
                    break
            
            if tab_mind:
                # Pega todos os cookies da aba
                cookies = tab_mind.cookies()
                auth_data["mind7_cookies"] = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        except Exception as e:
            print(f"    [!] Erro ao obter cookies do Mind7: {e}")

        # Extraindo Headers do Itau (conectando a porta correspondente do Itau)
        try:
            co_itau = ChromiumOptions().set_local_port(itau_port)
            co_itau.set_browser_path(browser_path)
            page_itau = ChromiumPage(co_itau)
            
            tab_itau = None
            for tab_id in page_itau.get_tabs():
                t = page_itau.get_tab(tab_id)
                if "credlineitau.com.br" in t.url:
                    tab_itau = t
                    break
            
            if tab_itau:
                # Começa a ouvir e espera um pacote que tenha o charon para roubar o token
                tab_itau.listen.start("charon")
                # Faz uma pequena acao ou espera o proximo evento natural
                # Mas aqui, o ideal é que o usuario ja tenha feito uma consulta antes.
                # Se nao tiver, tentamos ler do ultimo pacote que passou se o DrissionPage permitir.
                
                # Fallback: se nao tiver pacote novo, usamos os headers que pegamos no espião.
                # Para ser 100% automático, vamos tentar pegar do storage via JS:
                headers_json = tab_itau.run_js("return JSON.stringify(sessionStorage)")
                # (O Itau costuma guardar tokens no SessionStorage ou Cookies)
                pass
        except:
            pass
            
        return auth_data

    def consultar_mind7_api(self, telefone: str, cookies_str: str, nome_whatsapp: str = "") -> dict:
        """
        Consulta o Mind7 diretamente via HTTP POST.
        """
        url = "https://mind-7.org/painel/consultas/celular/search.php"
        
        # Formata o telefone para o padrão do Mind7: (16)+99999-9999
        # Remove tudo que não é número
        num = re.sub(r'\D', '', telefone)
        if len(num) >= 13 and num.startswith("55"):
            num = num[2:]
            
        if len(num) == 11:
            tel_formatado = f"({num[0:2]})+{num[2:7]}-{num[7:]}"
        elif len(num) == 10:
            tel_formatado = f"({num[0:2]})+{num[2:6]}-{num[6:]}"
        else:
            tel_formatado = telefone

        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Cookie": cookies_str,
            "User-Agent": "Automacao-V3 (Mind7 usage-limited client)",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://mind-7.org/painel/consultas/celular/"
        }
        
        payload = {
            "documento": tel_formatado,
            "tipo_consulta": "celular",
            "termos_aceitos": "on"
        }
        
        def send():
            try:
                response = self.session.post(
                    url, headers=headers, data=payload, timeout=30, allow_redirects=False,
                )
            except requests.RequestException as exc:
                raise Mind7Unavailable("Resposta Mind7 incerta; confira antes de reenviar.") from exc
            # Persistir a recusa antes de liberar o lock para outro processo.
            if response.status_code in (403, 429):
                mark_mind7_blocked(f"HTTP {response.status_code}")
                raise Mind7AccessBlocked("Mind7 recusou a consulta; aguarde liberação do provedor.")
            soup = BeautifulSoup(response.text, "html.parser")
            try:
                _verificar_bloqueio_mind7(None, soup.get_text(" ", strip=True),
                                         titulo=soup.title.get_text(strip=True) if soup.title else "")
            except Mind7Unavailable as exc:
                mark_mind7_blocked("Resposta HTTP exige validação no navegador")
                raise Mind7AccessBlocked("Mind7 exige validação manual; consulta HTTP suspensa.") from exc
            return response
        response = reserve_mind7_request(send=send)
        print(f"  Mind7 API: HTTP {response.status_code}, bytes={len(response.text)}")
        
        if response.status_code != 200:
            raise Mind7Unavailable(f"Mind7 indisponível: HTTP {response.status_code}")
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        candidatos = []
        for cpf_link in soup.find_all('a', href=re.compile(r'documento=')):
            cpf = re.sub(r'\D', '', cpf_link.text.strip())
            if len(cpf) != 11:
                continue

            nome = "Desconhecido"
            bloco = cpf_link.find_parent(['tr', 'div', 'li']) or soup
            nome_tag = bloco.find('small', string=re.compile(r'Nome:', re.I))
            if nome_tag:
                proximo = nome_tag.find_next(['span', 'strong', 'b'])
                if proximo:
                    nome = proximo.get_text(" ", strip=True)
            else:
                textos = [t.strip() for t in bloco.stripped_strings if t.strip()]
                for texto in textos:
                    if len(texto) > 3 and not re.search(r'\d', texto) and "nome" not in texto.lower():
                        nome = texto
                        break

            candidatos.append({"cpf": cpf, "nome_completo": nome, "cep": "", "numero_casa": ""})

        if not candidatos:
            print("  Mind7 API: nenhum CPF candidato encontrado no HTML retornado.")
            return None

        if nome_whatsapp:
            for candidato in candidatos:
                if comparar_primeiro_nome(nome_whatsapp, candidato["nome_completo"]):
                    return candidato
            print(f"  Mind7 API: {len(candidatos)} candidato(s), nenhum contem nome compativel com WhatsApp.")
            return None

        return candidatos[0]
        
        return None

    def consultar_itau_api(self, cpf: str, auth_headers: dict) -> bool:
        """
        Consulta o Itau diretamente via HTTP POST JSON.
        """
        url = "https://apicd.cloud.itau.com.br/charon/brr13ot8/acc194377a8ea7eb"
        
        # Usa os headers capturados pelo espião (precisam ser atualizados dinamicamente depois)
        headers = {
            "Authorization": auth_headers.get("Authorization"),
            "x-itau-apikey": auth_headers.get("x-itau-apikey"),
            "x-charon-session": auth_headers.get("x-charon-session"),
            "x-itau-correlationID": auth_headers.get("x-itau-correlationID"),
            "x-itau-flowID": auth_headers.get("x-itau-flowID"),
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0"
        }
        
        payload = {
            "clientDocument": cpf,
            "sellerDocument": "20289806000159" # Documento do lojista (fixo)
        }
        
        response = self.session.post(url, headers=headers, json=payload)
        
        if response.status_code == 200:
            data = response.json()
            # GREEN geralmente significa aprovado/limpo no simulador Credline
            return data.get("statusAnalysis") == "GREEN"
            
        return False
