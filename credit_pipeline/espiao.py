import time
from DrissionPage import ChromiumPage, ChromiumOptions
import json

def iniciar_espionagem():
    print("Conectando ao navegador existente (Edge na porta 9223)...")
    co = ChromiumOptions()
    co.set_local_port(9223)
    page = ChromiumPage(co)
    
    print("Radar focado no Mind-7 e Itau...")
    tabs = page.get_tabs()
    for tab_id in tabs:
        page.get_tab(tab_id).listen.start()

    try:
        while True:
            for tab_id in tabs:
                t = page.get_tab(tab_id)
                packet = t.listen.wait(timeout=0.1)
                if packet:
                    url = packet.request.url
                    if "search.php" in url or "charon" in url:
                        print(f"\n[!] ALVO CAPTURADO: {url}")
                        with open("espiao_final.txt", "a", encoding="utf-8") as f:
                            f.write(f"\n\nURL: {url}\n")
                            f.write(f"METHOD: {packet.request.method}\n")
                            f.write(f"HEADERS: {json.dumps(dict(packet.request.headers), indent=2)}\n")
                            f.write(f"PAYLOAD: {packet.request.postData}\n")
                            resp_body = getattr(getattr(packet, 'response', None), 'body', '') or ''
                            f.write(f"RESPONSE: {str(resp_body)[:2000]}\n")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[✓] Captura finalizada!")

if __name__ == "__main__":
    # Limpa o arquivo anterior
    with open("espiao_final.txt", "w", encoding="utf-8") as f:
        f.write("=== LOG DE ESPIONAGEM DE APIS ===\n")
        
    iniciar_espionagem()
