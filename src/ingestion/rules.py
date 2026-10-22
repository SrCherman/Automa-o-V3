"""
rules.py - Algoritmos Completos do MODELO BASE para Sanitização, Validação e Matching Fonético/Multi-Token de Nomes
"""

import re
import unicodedata
from typing import Tuple, List, Optional

_EMOJI_RE = re.compile("[\U00010000-\U0001FFFF\U00002600-\U000027BF\U0000FE00-\U0000FE0F\U0000200D]+", flags=re.UNICODE)

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

_NOISE_WORDS = {
    "dr", "dra", "sr", "sra", "adv", "advogado", "advogada", "eng", "engenheiro",
    "prof", "professor", "pastor", "pr", "bispo", "pai", "mae", "filho", "neto", "junior", "jr",
    "ltda", "me", "eireli", "sa", "s.a", "cooperativa", "auto", "pecas", "mecanica", "servicos",
    "consultoria", "rh", "transportes", "loja", "salao", "studio", "clinica", "online",
    "digitando", "agora", "ontem", "mensagens", "nao", "lidas", "visto", "ultimo",
    "whatsapp", "contato", "cliente", "financeiro", "atendimento", "compro", "vendo",
    "veiculos", "veiculo", "carros", "carro", "vendas", "frete", "transporte", "beleza",
    "estetica", "barbearia", "pizzaria", "restaurante", "mkt", "digital", "gerente",
    "modas", "boutique", "entregas", "express", "deposito", "supermercado", "farmacia",
    "drogaria", "contabilidade", "construtora", "serralheria", "marcenaria", "grafica",
    "escolar", "curso", "academia", "odonto", "vet", "pet", "petshop", "taxi", "uber",
    "driver", "moto", "motoboy", "guincho", "reboque", "chaveiro", "lava", "jato",
    "doces", "bolos", "buffet", "eventos", "fotografia", "fotos", "dj", "som", "iluminacao",
    "locacao", "aluguel", "venda", "reforma", "pintura", "eletricista", "encanador",
    "gesso", "pisos", "vidracaria", "seguros", "credito", "financiamento", "emprestimo",
    "consorcio", "multas", "despachante", "cartorio", "engenharia", "arquitetura", "design",
    "agencia", "turismo", "viagens", "pousada", "hotel", "horti", "fruti", "acougue",
    "padaria", "confeitaria", "sorveteria", "lanchonete", "hamburgueria", "pastelaria",
    "espetinho", "bar", "distribuidora", "adega", "bebidas", "gas", "agua", "associacao",
    "sindicato", "igreja", "paroquia", "ministerio", "grupo", "equipe", "canal", "page",
    "oficial", "oi", "ola", "hi", "hello", "ok", "tudo", "bem", "obrigado", "obrigada",
    "sim", "top", "show", "bom", "dia", "tarde", "noite", "casa", "ap", "apartamento",
    "terreno", "lote", "sitio", "chacara", "fazenda", "multimarcas", "motors", "center",
    "mecanico", "pintor", "funilaria", "lanternagem", "estofados", "higienizacao",
    "polimento", "solar", "energia", "vendedor", "vendedora", "representante", "corretor",
    "corretora", "desconto", "oferta", "promocao", "promocional", "qualidade", "garantia",
    "melhor", "preco", "orcamento", "gratis", "gratuitos", "zap"
}

_PREPOSITIONS = {"de", "da", "do", "das", "dos", "e", "a", "o"}

FRASES_E_RUIDOS_PROIBIDOS = {
    "deus e fiel", "deus e amor", "deus e bom", "fe em deus", "paz e bem",
    "cai corintia", "vai corinthians", "vai palmeiras", "sou flamengo", "mengao",
    "visto por ultimo", "mensagem nao lida", "mensagens nao lidas", "online", "digitando",
    "em busca da felicidade", "tudo passa", "agradecer sempre", "obrigado meu deus",
    "so vitoria", "amem", "deus no comando", "jesus te ama", "gratidao",
    "sempre em frente", "a vida e bela", "deus abencoe", "status whatsapp",
    "cliente", "atendimento", "comercial", "financeiro", "vendas", "suporte",
    "sem nome", "nao informado", "desconhecido", "contato", "usuario"
}

def extrair_nome_real(nome: str) -> str:
    """
    Remove partes de empresa ou marcações que vêm após delimitadores comuns.
    Ex: "Antonio - Sociis RH" -> "Antonio"
        "Carlos | Sociis" -> "Carlos"
        "Ana (Sociis)" -> "Ana"
    """
    if not nome:
        return ""
    nome = re.sub(r"\s*\(.*?\)", "", nome)
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
        
    nome = _EMOJI_RE.sub("", nome)
    nome = unicodedata.normalize("NFKD", nome)
    nome = "".join(c for c in nome if not unicodedata.combining(c))
    nome = re.sub(r"[^a-zA-Z\s]", " ", nome)
    
    tokens = nome.lower().split()
    tokens_filtrados = []
    for t in tokens:
        if t in _PREPOSITIONS or t in _NOISE_WORDS:
            continue
        if len(t) < 2:  # Ignorar iniciais isoladas
            continue
        tokens_filtrados.append(t)
        
    return tokens_filtrados

def normalizar_fonetica(w: str) -> str:
    if not w:
        return ""
    w = w.lower()
    w = w.replace("y", "i").replace("z", "s").replace("w", "v").replace("ph", "f").replace("h", "")
    return re.sub(r"(.)\1+", r"\1", w)

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
    tw_norm = normalizar_fonetica(tw)
    tm_norm = normalizar_fonetica(tm)
    
    if tw_norm == tm_norm:
        return 1.0
        
    sim = string_similarity(tw_norm, tm_norm)
    if sim >= 0.82:
        return sim
        
    tw_sem_dim = normalizar_fonetica(remover_diminutivo(tw))
    tm_sem_dim = normalizar_fonetica(remover_diminutivo(tm))
    if tw_sem_dim != tw_norm or tm_sem_dim != tm_norm:
        sim_dim = string_similarity(tw_sem_dim, tm_sem_dim)
        if sim_dim >= 0.82:
            return sim_dim * 0.95
            
    if len(tw_norm) >= 3 and tm_norm.startswith(tw_norm):
        ratio = len(tw_norm) / len(tm_norm)
        if ratio >= 0.4:
            return 0.85
            
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
    Usa a inteligência completa do MODELO BASE:
    - Se o nome tem 1 token: compara o primeiro nome ou tokens secundários.
    - Se tem 2+ tokens: exige que o primeiro nome E pelo menos um sobrenome correspondam.
    """
    tokens_w = limpar_nome_inteligente(nome_whats, remover_separadores=True)
    tokens_m = limpar_nome_inteligente(nome_mind7, remover_separadores=False)
    
    if not tokens_w or not tokens_m:
        return 0.0
        
    # Se o nome do WhatsApp tem apenas 1 token (ex: "Carlos" ou "Zeca")
    if len(tokens_w) == 1:
        tw = tokens_w[0]
        best_first_name_score = token_match_score(tw, tokens_m[0])
        if best_first_name_score >= 0.82:
            return best_first_name_score
        for tm in tokens_m[1:]:
            score = token_match_score(tw, tm)
            if score >= 0.85:
                return score * 0.90
        return 0.0

    # Se o nome do WhatsApp tem 2 ou mais tokens (ex: "Carlos Silva")
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

def normalizar_para_comparacao(texto: str) -> str:
    if not texto: return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z\s]", " ", s)
    return " ".join(s.lower().split())

def sanitizar_e_validar_contato(raw_phone: str, whatsapp_name: str, contact_name: str) -> Tuple[bool, str, str, str]:
    nome_candidato = contact_name if (contact_name and str(contact_name).strip().upper() != "N/A") else whatsapp_name
    if not nome_candidato: return False, "", "", "Nome ausente/vazio"

    nome_str = str(nome_candidato).strip()
    if nome_str.upper() in {"N/A", "NONE", "NULL", "DESCONHECIDO", ".", "-", "_", "N / A", "NA"}:
        return False, "", "", "Nome é N/A ou variação nula"

    letras_reais = re.findall(r"[a-zA-ZáàâãéèêíïóôõöúçñÁÀÂÃÉÈÍÏÓÔÕÖÚÇÑ]", nome_str)
    if not letras_reais or len(letras_reais) < 2:
        return False, "", "", "Nome composto apenas por emojis, símbolos, números ou caracteres especiais"

    nome_norm = normalizar_para_comparacao(nome_str)
    if any(frase in nome_norm for frase in FRASES_E_RUIDOS_PROIBIDOS):
        return False, "", "", f"Nome é uma frase de status/religiosa/futebol/ruído: '{nome_str}'"

    palavras = nome_norm.split()
    if len(palavras) >= 4 and any(p in {"que", "com", "para", "como", "esta", "estou", "estamos", "fazendo", "muito"} for p in palavras):
        return False, "", "", f"Nome é uma frase longa: '{nome_str}'"

    if len(palavras) == 1:
        token = palavras[0]
        if len(token) <= 1: return False, "", "", "Apelido de caractere único"
        if token in {"sim", "nao", "top", "topa", "ola", "oi", "hey", "kkk", "rsrs"}:
            return False, "", "", "Apelido é uma interjeição/gíria inválida"

    num_limpo = re.sub(r"\D", "", str(raw_phone or ""))
    if num_limpo.startswith("55") and len(num_limpo) > 11: num_limpo = num_limpo[2:]
    if len(num_limpo) > 11: num_limpo = num_limpo[-11:]
    if len(num_limpo) < 10 or len(num_limpo) > 11:
        return False, "", "", f"Telefone inválido após sanitização: '{raw_phone}' -> '{num_limpo}'"

    return True, num_limpo, " ".join(nome_str.strip().split()), "OK"
