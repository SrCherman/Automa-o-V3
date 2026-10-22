from unittest.mock import Mock

from openpyxl import Workbook

from credit_pipeline import processar as legacy
from src.automation.c6_scraper import C6Unavailable


def test_legacy_enriches_all_cpfs_and_preserves_itau(monkeypatch):
    wb = Workbook()
    ws = wb.active
    ws.append(["Data", "Nome", "Telefone", "CPF", "Nome Mind7", "Status Itaú", "Obs", "CEP", "Numero", "Idade", "Valor"])
    ws.append(["", "Teste", "45999990000", "12345678901", "Teste", "REPROVADO", "Resultado anterior", "85800000", "20", "36 anos", ""])
    lookup = Mock(return_value={"birth_date": "1990-01-01"})
    monkeypatch.setattr(legacy, "consultar_mind7_cpf", lookup)
    monkeypatch.setattr(legacy, "_salvar", lambda _: None)
    legacy.etapa_enriquecimento(wb, ws, Mock())
    legacy.etapa_enriquecimento(wb, ws, Mock())
    assert ws.cell(2, 6).value == "REPROVADO"
    assert ws.cell(2, 10).value == "36 anos"  # Histórico não vira data fictícia.
    assert ws.cell(2, legacy._coluna(ws, "Data de Nascimento")).value == "1990-01-01"
    assert lookup.call_count == 1


def test_legacy_uncertain_submit_never_repeats(monkeypatch):
    wb = Workbook()
    ws = wb.active
    ws.append(["Data", "Nome", "Telefone", "CPF", "Nome Mind7", "Status Itaú", "Obs", "CEP", "Numero", "Idade", "Valor"])
    ws.append(["", "Teste", "45999990000", "12345678901", "Teste", "REPROVADO"])
    ws.cell(2, legacy._coluna(ws, "Data de Nascimento"), "1990-01-01")
    ws.cell(2, legacy._coluna(ws, "Status Enriquecimento"), "CONCLUIDO")
    def submit(*args):
        args[-1]()
        raise TimeoutError("resultado perdido")
    lookup = Mock(side_effect=submit)
    monkeypatch.setattr(legacy, "consultar_c6_cpf", lookup)
    monkeypatch.setattr(legacy, "_salvar", lambda _: None)
    legacy.etapa_c6(wb, ws, Mock())
    legacy.etapa_c6(wb, ws, Mock())
    assert ws.cell(2, legacy._coluna(ws, "Status C6")).value == "CONFERIR RESULTADO"
    assert lookup.call_count == 1
    assert lookup.call_args.args[4:6] == ("SEO7H39", "130.000,00")
    assert lookup.call_args.args[6]["entry_value"] == ""


def test_legacy_incomplete_cache_does_not_hide_new_birth_date(monkeypatch):
    wb = Workbook()
    ws = wb.active
    ws.append(["Data", "Nome", "Telefone", "CPF", "Nome Mind7", "Status Itaú", "Obs", "CEP", "Numero", "Idade", "Valor"])
    ws.append(["", "Primeiro", "45999990000", "12345678901"])
    ws.append(["", "Segundo", "45999990001", "12345678901"])
    ws.cell(2, legacy._coluna(ws, "Status Enriquecimento"), "SEM DADOS")
    lookup = Mock(return_value={"birth_date": "1990-01-01"})
    monkeypatch.setattr(legacy, "consultar_mind7_cpf", lookup)
    monkeypatch.setattr(legacy, "_salvar", lambda _: None)
    legacy.etapa_enriquecimento(wb, ws, Mock())
    assert lookup.call_count == 1
    assert ws.cell(3, legacy._coluna(ws, "Data de Nascimento")).value == "1990-01-01"


def test_legacy_login_pause_does_not_consume_c6_attempts(monkeypatch):
    wb = Workbook()
    ws = wb.active
    ws.append(["Data", "Nome", "Telefone", "CPF", "Nome Mind7", "Status Itaú", "Obs", "CEP", "Numero", "Idade", "Valor"])
    ws.append(["", "Teste", "45999990000", "12345678901"])
    ws.cell(2, legacy._coluna(ws, "Data de Nascimento"), "1990-01-01")
    ws.cell(2, legacy._coluna(ws, "Status Enriquecimento"), "CONCLUIDO")
    lookup = Mock(side_effect=C6Unavailable("Login C6 pendente"))
    monkeypatch.setattr(legacy, "consultar_c6_cpf", lookup)
    monkeypatch.setattr(legacy, "_salvar", lambda _: None)
    for _ in range(4):
        legacy.etapa_c6(wb, ws, Mock())
    assert lookup.call_count == 4
    assert ws.cell(2, legacy._coluna(ws, "Tentativas C6")).value == 0
