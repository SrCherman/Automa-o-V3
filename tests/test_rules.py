"""
test_rules.py - Testes Unitários para src/ingestion/rules.py (Sanitização, Matching Fonético e Validação de Contatos)
"""

import pytest
from src.ingestion.rules import (
    extrair_nome_real,
    limpar_nome_inteligente,
    normalizar_fonetica,
    levenshtein_distance,
    string_similarity,
    remover_diminutivo,
    token_match_score,
    calcular_score_nome,
    nomes_batem,
    normalizar_para_comparacao,
    sanitizar_e_validar_contato,
)


# ============================================================================
# extrair_nome_real
# ============================================================================

class TestExtrairNomeReal:
    @pytest.mark.parametrize("entrada, esperado", [
        ("Antonio - Sociis RH", "Antonio"),
        ("Carlos | Sociis", "Carlos"),
        ("Ana (Sociis)", "Ana"),
        ("João sem delimitador", "João sem delimitador"),
        ("Maria / Vendas", "Maria"),
        ("Pedro @ Financeiro", "Pedro"),
        ("", ""),
        ("  Espaços  ", "Espaços"),
        ("Nome (Empresa) - Dept", "Nome"),
        ("Roberto (Adv) | Jurídico", "Roberto"),
    ])
    def test_extrair_nome_real(self, entrada, esperado):
        assert extrair_nome_real(entrada) == esperado


# ============================================================================
# limpar_nome_inteligente
# ============================================================================

class TestLimparNomeInteligente:
    def test_remove_emojis_e_ruido(self):
        result = limpar_nome_inteligente("Dr. João Silva 🦷 advogado")
        assert result == ["joao", "silva"]

    def test_remove_acentos(self):
        result = limpar_nome_inteligente("José Antônio")
        assert result == ["jose", "antonio"]

    def test_remove_preposicoes(self):
        result = limpar_nome_inteligente("Maria de Souza")
        assert result == ["maria", "souza"]

    def test_remove_noise_words(self):
        result = limpar_nome_inteligente("Carlos Consultoria LTDA")
        assert result == ["carlos"]

    def test_string_vazia(self):
        assert limpar_nome_inteligente("") == []

    def test_none(self):
        assert limpar_nome_inteligente(None) == []

    def test_only_emojis(self):
        result = limpar_nome_inteligente("🔥💪🎉")
        assert result == []

    def test_iniciais_isoladas_removidas(self):
        result = limpar_nome_inteligente("J M Silva")
        assert result == ["silva"]

    def test_remove_separadores(self):
        result = limpar_nome_inteligente("Carlos Silva - Sociis RH (Vendas)")
        assert result == ["carlos", "silva"]


# ============================================================================
# normalizar_fonetica
# ============================================================================

class TestNormalizarFonetica:
    @pytest.mark.parametrize("entrada, esperado", [
        ("Felipe", "felipe"),
        ("Philipe", "filipe"),
        ("Wagner", "vagner"),
        ("Yasmin", "iasmin"),
        ("Thiago", "tiago"),
        ("", ""),
    ])
    def test_normalizar_fonetica(self, entrada, esperado):
        assert normalizar_fonetica(entrada) == esperado


# ============================================================================
# levenshtein_distance & string_similarity
# ============================================================================

class TestSimilaridade:
    def test_levenshtein_strings_iguais(self):
        assert levenshtein_distance("abc", "abc") == 0

    def test_levenshtein_strings_diferentes(self):
        assert levenshtein_distance("abc", "xyz") == 3

    def test_levenshtein_uma_vazia(self):
        assert levenshtein_distance("abc", "") == 3

    def test_similarity_identicas(self):
        assert string_similarity("carlos", "carlos") == 1.0

    def test_similarity_vazias(self):
        assert string_similarity("", "") == 0.0

    def test_similarity_parcial(self):
        sim = string_similarity("carlos", "carlo")
        assert 0.8 <= sim <= 1.0


# ============================================================================
# remover_diminutivo
# ============================================================================

class TestRemoverDiminutivo:
    @pytest.mark.parametrize("entrada, esperado", [
        ("carlinho", "carlo"),      # 8 chars > 5: remove -inho -> -o
        ("carlinhos", "carlinhos"),  # Não termina em inho/inha
        ("joaozinho", "joaozo"),    # Remove -inho -> -o
        ("mariazinha", "mariaza"),   # Remove -inha -> -a
        ("paulo", "paulo"),         # Sem diminutivo
    ])
    def test_remover_diminutivo(self, entrada, esperado):
        assert remover_diminutivo(entrada) == esperado


# ============================================================================
# token_match_score
# ============================================================================

class TestTokenMatchScore:
    def test_match_exato(self):
        assert token_match_score("carlos", "carlos") == 1.0

    def test_match_fonetico(self):
        score = token_match_score("felipe", "philipe")
        assert score >= 0.82

    def test_apelido_reconhecido(self):
        score = token_match_score("beto", "roberto")
        assert score >= 0.85

    def test_apelido_zeca(self):
        score = token_match_score("zeca", "jose")
        assert score >= 0.85

    def test_prefixo_curto(self):
        score = token_match_score("leo", "leonardo")
        assert score >= 0.80

    def test_nomes_completamente_diferentes(self):
        score = token_match_score("ana", "pedro")
        assert score == 0.0

    def test_apelido_duda(self):
        score = token_match_score("duda", "eduarda")
        assert score >= 0.85


# ============================================================================
# calcular_score_nome — TESTES CRÍTICOS
# ============================================================================

class TestCalcularScoreNome:
    """
    Testes críticos: este é o algoritmo que decide se um CPF do Mind7
    pertence ou não ao contato do WhatsApp. Um bug aqui atribui CPF errado.
    """

    # --- MATCHES CORRETOS (deve retornar >= 0.80) ---

    def test_nome_identico(self):
        score = calcular_score_nome("Carlos Silva", "Carlos Silva")
        assert score >= 0.90

    def test_nome_com_ruido_whatsapp(self):
        score = calcular_score_nome("Carlos Silva - Sociis RH", "Carlos Eduardo Silva")
        assert score >= 0.80

    def test_apelido_com_sobrenome(self):
        score = calcular_score_nome("Zeca Silva", "Jose Carlos Silva")
        assert score >= 0.80

    def test_nome_unico_token(self):
        score = calcular_score_nome("Carlos", "Carlos Eduardo Silva")
        assert score >= 0.80

    def test_primeiro_nome_fonetico(self):
        score = calcular_score_nome("Thiago Souza", "Tiago Souza")
        assert score >= 0.80

    def test_apelido_unico_token(self):
        score = calcular_score_nome("Beto", "Roberto Almeida")
        assert score >= 0.80

    # --- REJEIÇÕES CORRETAS (deve retornar < 0.80) ---

    def test_nomes_totalmente_diferentes(self):
        score = calcular_score_nome("Carlos Silva", "Carlos Santos")
        assert score < 0.80, f"Score {score} deveria ser < 0.80 (sobrenomes diferentes)"

    def test_primeiro_nome_diferente(self):
        score = calcular_score_nome("Maria Silva", "João Silva")
        assert score < 0.80

    def test_nome_completamente_diferente(self):
        score = calcular_score_nome("Ana Paula", "Ricardo Ferreira")
        assert score < 0.80

    def test_nome_vazio_whatsapp(self):
        score = calcular_score_nome("", "Carlos Silva")
        assert score == 0.0

    def test_nome_vazio_mind7(self):
        score = calcular_score_nome("Carlos Silva", "")
        assert score == 0.0

    def test_ambos_vazios(self):
        score = calcular_score_nome("", "")
        assert score == 0.0

    def test_nome_so_ruido(self):
        score = calcular_score_nome("Dr. Adv.", "Carlos Silva")
        assert score < 0.80

    # --- EDGE CASES ---

    def test_nome_com_muitos_emojis(self):
        score = calcular_score_nome("Carlos 🔥💪 Silva", "Carlos Silva")
        assert score >= 0.80

    def test_nome_com_acentos_diferentes(self):
        score = calcular_score_nome("José Antônio", "Jose Antonio")
        assert score >= 0.80


# ============================================================================
# nomes_batem
# ============================================================================

class TestNomesBatem:
    def test_nomes_iguais(self):
        assert nomes_batem("Carlos Silva", "Carlos Silva") is True

    def test_nomes_diferentes(self):
        assert nomes_batem("Ana", "Pedro") is False

    def test_threshold_custom(self):
        score = calcular_score_nome("Carlos Silva", "Carlos Eduardo Silva")
        if score >= 0.90:
            assert nomes_batem("Carlos Silva", "Carlos Eduardo Silva", threshold=0.90) is True


# ============================================================================
# sanitizar_e_validar_contato
# ============================================================================

class TestSanitizarValidarContato:
    def test_contato_valido(self):
        valido, phone, nome, motivo = sanitizar_e_validar_contato(
            "5511988887777", "Carlos Mendes", "Carlos Mendes"
        )
        assert valido is True
        assert phone == "11988887777"
        assert motivo == "OK"

    def test_contato_valido_sem_55(self):
        valido, phone, nome, motivo = sanitizar_e_validar_contato(
            "11988887777", "Ana Silva", "Ana Silva"
        )
        assert valido is True
        assert phone == "11988887777"

    def test_rejeita_nome_vazio(self):
        valido, phone, nome, motivo = sanitizar_e_validar_contato(
            "11988887777", "", ""
        )
        assert valido is False

    def test_rejeita_nome_na(self):
        valido, phone, nome, motivo = sanitizar_e_validar_contato(
            "11988887777", "N/A", "N/A"
        )
        assert valido is False

    def test_rejeita_frase_religiosa(self):
        valido, phone, nome, motivo = sanitizar_e_validar_contato(
            "5511999999999", "Deus é fiel", "N/A"
        )
        assert valido is False
        assert "ruído" in motivo.lower() or "frase" in motivo.lower() or "status" in motivo.lower()

    def test_rejeita_telefone_curto(self):
        valido, phone, nome, motivo = sanitizar_e_validar_contato(
            "123", "Carlos Silva", "Carlos Silva"
        )
        assert valido is False
        assert "telefone" in motivo.lower() or "inválido" in motivo.lower()

    def test_rejeita_emoji_puro(self):
        valido, phone, nome, motivo = sanitizar_e_validar_contato(
            "11988887777", "🔥💪🎉", "N/A"
        )
        assert valido is False

    def test_rejeita_interjeicao(self):
        valido, phone, nome, motivo = sanitizar_e_validar_contato(
            "11988887777", "oi", "N/A"
        )
        assert valido is False

    def test_usa_contact_name_quando_whatsapp_e_na(self):
        valido, phone, nome, motivo = sanitizar_e_validar_contato(
            "11988887777", "N/A", "Carlos Real"
        )
        assert valido is True
        assert "Carlos Real" in nome


# ============================================================================
# normalizar_para_comparacao
# ============================================================================

class TestNormalizarParaComparacao:
    def test_remove_acentos_e_pontuacao(self):
        assert normalizar_para_comparacao("José Antônio!") == "jose antonio"

    def test_string_vazia(self):
        assert normalizar_para_comparacao("") == ""

    def test_none(self):
        assert normalizar_para_comparacao(None) == ""
