"""Carga inicial a partir da planilha DRE (com uma planilha de mentira
que tem o mesmo formato da real)."""

from datetime import datetime
from decimal import Decimal

import openpyxl
import pytest

from apps.cadastros.models import Parceiro, Produto
from apps.contratos.models import Contrato
from apps.estoque import services as estoque
from apps.integracao import planilha as P


def _planilha():
    wb = openpyxl.Workbook()
    est = wb.active
    est.title = "Estoque"
    est.append([None, None, "ESTOQUE PRODUTO - GLICERINA", None, None, None, None, "INICIAL"])
    est.append([None, None, "GLICERINA 80% ", None, 80000, 3.2, 256000, "Vale Bio"])
    est.append([None, None, None, None, None, "TOTAL:", 1])
    est.append([None, None, "ESTOQUE PRODUTO - GLICERINA", None, None, None, None, "FINAL"])
    est.append([None, None, "PRODUTO", None, "QUANTIDADE", "VALOR UNITARIO", "VALOR TOTAL", "LOCAL"])
    est.append([None, None, "GLICERINA 80% ", None, 120000, 4, 480000, "Vale Bio"])
    est.append([None, None, "GLICERINA BI-DESTILADA USP (GRANEL)", None, 83954.5, 5.575149, 1, "Aroma"])
    est.append([None, None, "GLICERINA BI-DEST. USP ( TB 250Kg Azul)", None, 4750, 6.5697, 1, "JT Ecobio"])
    est.append([None, None, "IBC - 1250Kg - (EMBALAGEM NOVA)", None, 2, 962.5, 1925, "JT Ecobio"])
    est.append([None, None, None, None, None, "TOTAL:", 1])
    est.append([None, None, "M. INTERNO", None, 338960, 336330, 2630])

    ctt = wb.create_sheet("Saldo Ctt Glicerina")
    ctt.append([None, None, None, None, None, None, None, None, None, None, "Data", datetime(2026, 11, 4)])
    ctt.append([None, None, "SALDOS CONTRATOS", None, None, None, None, None, None, None, "Mês:"])
    ctt.append([None, None, None, None, None, None, None, None, None, None, datetime(2026, 10, 1)])
    ctt.append([None, "CONTRATOS COMPRAS"])
    ctt.append([None, "PRODUTO:", "GLICEIRNA 80%"])
    ctt.append([None, "Nº CONTRATO", "FORNECEDORES", "QTDE CTT", "SALDO CTT", "PÇ BRUTO", "PÇ NET", "FRETE", "FRETE NET", "MÊS", "OBS"])
    ctt.append([None, "734/2026", "Oleoplan Rondonia", 97120, 0, 2.4, 1.9, 0.75, 0.66, datetime(2026, 10, 1), "Saída p/ Industria"])
    ctt.append([None, "814/2026", "Cofco International", 257260, 207380, 3.63, 2.9, 0.54, 0.47, datetime(2026, 11, 1)])
    ctt.append([None, "TOTAL", None, 354380, 207380])
    ctt.append([None, "PRODUTO:", "GLICEIRNA BI-DEST. FRACIONADA (IBC 1.250Kg e TB AZUL)"])
    ctt.append([None, "Estoque JT", "JT Ecobio ", 8250, 4750, 6.56, 6.56, 0, 0, datetime(2026, 10, 1), "GDB - TB 250Kg"])
    ctt.append([None, "SALDOS CONTRATOS VENDAS"])
    ctt.append([None, "PRODUTO:", "GLICEIRNA 80% - GRANEL  EXPORTACAO"])
    ctt.append([None, "P1501", "Milia Group Ltda", 487740, 28020, 3.4, 3.4, 0, 0, datetime(2026, 10, 1)])
    ctt.append([None, "PRODUTO:", "BI-DESTILADA - GRANEL  M. INTERNO"])
    ctt.append([None, 9291, "Maian Ind. de Prod. Quimicos", 175000, 175000, 7.63, 5.67, 0, 0, datetime(2026, 11, 1)])
    ctt.append([None, "0019-26", "K.S.A Sotero Aromas Corantes", 2500, 0, 9, 6.6, 0, 0, datetime(2026, 10, 1), "IBC 1.250kg"])
    ctt.append([None, "0099-26", "Empresa Que Nao Existe", 1000, 1000, 9, 6.6, 0, 0, datetime(2026, 10, 1)])
    return wb


@pytest.fixture
def base(db):
    for cod, nome, tipo in [("600", "GLICERINA 80%", "GLIC80"), ("958", "GLICERINA BIDESTILADA USP VEGETAL", "GBD_GRANEL"),
                            ("1292", "GLICERINA BIDESTILADA USP TB PLAS 250KG", "GBD_FRAC"),
                            ("1293", "GLICERINA BIDESTILADA USP IBC 1.250KG", "GBD_FRAC")]:
        Produto.objects.create(codigo=f"L{cod}", codigo_legado=cod, nome=nome, tipo=tipo, unidade="UN" if cod > "1" else "KG")
    for nome, papel in [("OLEOPLAN RONDONIA INDUSTRIA DE BIOCOMBUSTIVEIS", "eh_fornecedor"),
                        ("COFCO INTERNATIONAL BRASIL S.A.", "eh_fornecedor"),
                        ("MILIA GROUP LTDA", "eh_cliente"), ("BORGES MINIMERCADOS DA FAMILIA LTDA.", "eh_cliente"),
                        ("MAIAN INDUSTRIA DE PRODUTOS QUIMICOS LTDA", "eh_cliente"),
                        ("MAIANE PEREIRA DA SILVA", "eh_cliente"),
                        ("KSA INDUSTRIA E COMERCIO DE AROMAS E CORANTES LTDA", "eh_cliente"),
                        ("VALE LOGISTICA IND COMERCIAL E EXPORTACAO DE OLEOS LTDA", "eh_fornecedor"),
                        ("AROMA BIOQUIMICA LTDA", "eh_cliente")]:
        Parceiro.objects.create(razao_social=nome, **{papel: True})


def test_leitura_do_estoque_e_dos_contratos():
    wb = _planilha()
    assert P.mes_da_planilha(wb).isoformat() == "2026-10-01"  # e não 04/11, a data de emissão
    linhas = P.ler_estoque_final(wb)
    assert [(l.local, l.codigo_produto) for l in linhas] == [
        ("Vale Bio", "600"), ("Aroma", "958"), ("JT Ecobio", "1292"), ("JT Ecobio", None)]
    contratos = {c.numero: c for c in P.ler_contratos(wb)}
    assert contratos["734/2026"].codigo_produto == "600"  # "Saída p/ Industria" continua sendo 80%
    assert contratos["734/2026"].modalidade == "IND"
    assert contratos["P1501"].modalidade == "EXP" and contratos["P1501"].tipo == "V"
    assert contratos["9291"].codigo_produto == "958"
    assert contratos["0019-26"].codigo_produto == "1293"
    assert "TOTAL" not in contratos


def test_nomes_da_planilha_viram_palavras_comparaveis():
    assert P.palavras("K.S.A Sotero Aromas") == ["KSA", "SOTERO", "AROMAS"]
    assert P.palavras("M D - IND DE ADITIVOS")[:2] == ["MD", "ADITIVOS"]
    assert P.palavras("C JR Andrade Transportes")[:2] == ["JUNIOR", "ANDRADE"]


def test_carga_completa_e_repetivel(base):
    carga = P.CargaPlanilha(_planilha()).executar()
    assert carga.contagem["saldos iniciais lançados"] == 3
    vale = P.Local.objects.get(nome="Vale Logística (Ibaté)")
    glic = Produto.objects.get(codigo_legado="600")
    assert estoque.saldo(glic, vale) == Decimal("120000")
    assert estoque.custo_medio(glic, vale) == Decimal("4.0000")
    assert vale.parceiro.razao_social.startswith("VALE LOGISTICA")
    tb = Produto.objects.get(codigo_legado="1292")
    assert tb.unidade == "KG" and tb.controla_estoque  # fracionado passa a ser controlado em kg

    cofco = Contrato.objects.com_saldo().get(numero="814/2026")
    assert cofco.saldo == Decimal("207380") and cofco.status == "ABERTO"
    assert Contrato.objects.get(numero="734/2026").status == "FINAL"
    maian = Contrato.objects.get(numero="9291")
    assert maian.parceiro.razao_social.startswith("MAIAN INDUSTRIA")  # e não "MAIANE PEREIRA"
    assert Contrato.objects.get(numero="P1501").parceiro.razao_social == "MILIA GROUP LTDA"
    assert Contrato.objects.get(numero="0019-26").parceiro.razao_social.startswith("KSA")
    assert not Contrato.objects.filter(numero="Estoque JT").exists()
    assert any("Empresa Que Nao Existe" in a for a in carga.avisos)

    # Rodar de novo não duplica saldo nem contrato
    carga2 = P.CargaPlanilha(_planilha()).executar()
    assert carga2.contagem.get("saldos já carregados antes") == 3
    assert estoque.saldo(glic, vale) == Decimal("120000")
    assert Contrato.objects.filter(numero="814/2026").count() == 1


def test_capacidade_estimada_tem_folga():
    assert P.capacidade_estimada(Decimal("120000")) == Decimal("180000")
    assert P.capacidade_estimada(Decimal("2500")) == Decimal("10000")
