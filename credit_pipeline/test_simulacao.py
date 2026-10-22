import sys
import os
import time

# Adiciona o diretório do próprio script ao PATH de forma dinâmica
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from DrissionPage import ChromiumPage, ChromiumOptions

def main():
    print("--- INICIANDO NOVO TESTE DA SIMULAÇÃO CREDLINE ---")
    cpf_teste = os.environ["TEST_CPF"]
    
    # Conecta no Edge na porta 9224
    co = ChromiumOptions().set_local_port(9224)
    try:
        page = ChromiumPage(co)
        print("Conectado com sucesso ao Microsoft Edge na porta 9224!")
    except Exception as e:
        print(f"Erro ao conectar ao Edge na porta 9224: {e}")
        return

    import processar
    
    placa_teste = "IWD0H56"
    valor_teste = "120.000,00"
    
    print(f"Executando simulação para:")
    print(f"  CPF: {cpf_teste}")
    print(f"  Placa: {placa_teste}")
    print(f"  Valor: {valor_teste}")
    
    try:
        resultado = processar.consultar_credline(page, cpf_teste, placa_teste, valor_teste)
        print(f"\nRESULTADO DO TESTE: {resultado}")
    except Exception as e:
        print(f"Erro durante a execução do teste: {e}")

if __name__ == "__main__":
    main()
