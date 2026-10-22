"""
Utilitários de formatação e validação de dados usando Expressões Regulares (Regex) e limpeza de strings.
"""

import re
import unicodedata
from typing import Optional

def extrair_numero_telefone(texto: str) -> Optional[str]:
    """
    Busca um número de telefone no formato brasileiro dentro de um texto.
    Retorna apenas os dígitos numéricos (com DDD e 9 opcional) se encontrar.
    Exemplo de formatos suportados no texto: (11) 98765-4321, 11987654321, 11 87654321
    """
    # Regex para capturar DDD (2 dígitos) e número (8 ou 9 dígitos), aceitando formatações comuns.
    padrao = r'\(?\b([1-9]{2})\)?\s?(9?\d{4})[-.\s]?(\d{4})\b'
    match = re.search(padrao, texto)
    if match:
        # Junta os grupos capturados para retornar apenas os números limpos
        return "".join(match.groups())
    return None

def extrair_telefone_do_jid(remoteJid: str) -> Optional[str]:
    """
    Extrai o número de telefone a partir do remoteJid do WhatsApp.
    Formato do jid: 5541999998888@s.whatsapp.net
    Remove o código do país (qualquer que seja, vamos focar em remover o 55 do Brasil) 
    e retorna DDD + número (máximo 11 dígitos no Brasil).
    """
    if not remoteJid:
        return None
    # Pega apenas os dígitos antes do @
    numero_completo = remoteJid.split("@")[0]
    # Remove caracteres não numéricos
    numero_completo = re.sub(r'\D', '', numero_completo)
    
    if not numero_completo:
        return None
    
    # Se tem mais de 11 dígitos e começa com 55, remove o 55
    if numero_completo.startswith("55") and len(numero_completo) > 11:
        numero_completo = numero_completo[2:]
        
    # Se o número ainda tiver mais de 11 dígitos, pega os últimos 10 ou 11 (DDD + Num)
    if len(numero_completo) > 11:
        # Pega de trás pra frente (ex: remove códigos de operadora ou DDIs bizarros)
        numero_completo = numero_completo[-11:]
    
    return numero_completo

def limpar_nome(nome: str) -> str:
    """
    Limpa a string de nome, removendo espaços extras e deixando em letras minúsculas 
    para facilitar a comparação.
    """
    if not nome:
        return ""
    nome = unicodedata.normalize("NFKD", str(nome))
    nome = "".join(ch for ch in nome if not unicodedata.combining(ch))
    nome = re.sub(r"[^a-zA-Z\s]", " ", nome)
    return " ".join(nome.strip().lower().split())

def comparar_primeiro_nome(nome_whatsapp: str, nome_completo_mind7: str) -> bool:
    """
    Compara o primeiro nome do pushName do WhatsApp com o primeiro nome extraído do sistema Mind7.
    """
    if not nome_whatsapp or not nome_completo_mind7:
        return False
    
    tokens_whats = [t for t in limpar_nome(nome_whatsapp).split() if len(t) >= 3]
    tokens_mind7 = set(t for t in limpar_nome(nome_completo_mind7).split() if len(t) >= 3)

    return any(token in tokens_mind7 for token in tokens_whats)
