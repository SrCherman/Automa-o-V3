from DrissionPage import ChromiumPage, ChromiumOptions
import pytest

@pytest.mark.skip(reason="Diagnóstico manual: conecta a navegador real; fora da suíte offline.")
def test_tabs():
    co = ChromiumOptions()
    co.set_local_port(9222)
    try:
        page = ChromiumPage(co)
        print(f"Conectado ao navegador na porta 9222")
        tabs = page.get_tabs()
        print(f"Total de abas abertas: {len(tabs)}")
        for i, tab_id in enumerate(tabs):
            tab = page.get_tab(tab_id)
            print(f"Aba {i}: {tab.url}")
    except Exception as e:
        print(f"Erro: {e}")

if __name__ == "__main__":
    test_tabs()
