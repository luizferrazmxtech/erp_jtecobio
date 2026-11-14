from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.estoque import services as s
from apps.estoque.models import Configuracao, Movimentacao

D = Decimal


def entrada(prod, local, kg, custo, dia, **kw):
    return s.registrar_entrada(produto=prod, local=local, quantidade_kg=kg, custo_unitario=custo, data=dia, **kw)


def test_saldo_e_soma_das_movimentacoes(glicerina, aroma, dia):
    entrada(glicerina, aroma, 10000, "4.00", dia)
    entrada(glicerina, aroma, 5000, "5.00", dia)
    s.registrar_saida(produto=glicerina, local=aroma, quantidade_kg=3000, data=dia)
    assert s.saldo(glicerina, aroma) == D("12000")


def test_custo_medio_ponderado_e_saida_ao_custo_medio(glicerina, aroma, dia):
    entrada(glicerina, aroma, 10000, "4.00", dia)
    entrada(glicerina, aroma, 5000, "5.00", dia)
    assert s.custo_medio(glicerina, aroma) == D("4.3333")
    saida = s.registrar_saida(produto=glicerina, local=aroma, quantidade_kg=3000, data=dia)
    assert saida.custo_unitario == D("4.3333")
    assert saida.quantidade_kg == D("-3000")
    # Depois da saída o custo médio continua o mesmo
    assert s.custo_medio(glicerina, aroma) == D("4.3333")


def test_saida_maior_que_saldo_e_recusada(glicerina, aroma, dia):
    entrada(glicerina, aroma, 1000, "4", dia)
    with pytest.raises(ValidationError, match="Saldo insuficiente"):
        s.registrar_saida(produto=glicerina, local=aroma, quantidade_kg=1001, data=dia)
    assert Movimentacao.objects.count() == 1


def test_saldo_negativo_pode_ser_liberado_na_configuracao(glicerina, aroma, dia):
    Configuracao.objects.create(permitir_saldo_negativo=True)
    s.registrar_saida(produto=glicerina, local=aroma, quantidade_kg=500, data=dia)
    assert s.saldo(glicerina, aroma) == D("-500")


def test_custo_por_lote_exige_lote_e_usa_custo_dele(glicerina, aroma, dia):
    Configuracao.objects.create(metodo_custo=Configuracao.MetodoCusto.LOTE)
    barato = entrada(glicerina, aroma, 1000, "3.00", dia, lote_codigo="NF-A").lote
    entrada(glicerina, aroma, 1000, "5.00", dia, lote_codigo="NF-B")
    with pytest.raises(ValidationError, match="informe de qual lote"):
        s.registrar_saida(produto=glicerina, local=aroma, quantidade_kg=100, data=dia)
    saida = s.registrar_saida(produto=glicerina, local=aroma, quantidade_kg=800, data=dia, lote=barato)
    assert saida.custo_unitario == D("3.00")
    with pytest.raises(ValidationError, match="Saldo insuficiente"):
        s.registrar_saida(produto=glicerina, local=aroma, quantidade_kg=300, data=dia, lote=barato)


def test_transferencia_leva_o_custo_e_liga_as_pontas(glicerina, aroma, biobrotas, dia):
    entrada(glicerina, aroma, 1000, "4.50", dia)
    sai, ent = s.transferir(produto=glicerina, origem=aroma, destino=biobrotas, quantidade_kg=400, data=dia)
    assert s.saldo(glicerina, aroma) == D("600")
    assert s.saldo(glicerina, biobrotas) == D("400")
    assert ent.custo_unitario == D("4.5000")
    assert sai.par == ent and ent.par == sai


def test_transferencia_para_o_mesmo_local_e_recusada(glicerina, aroma, dia):
    entrada(glicerina, aroma, 1000, "4", dia)
    with pytest.raises(ValidationError):
        s.transferir(produto=glicerina, origem=aroma, destino=aroma, quantidade_kg=1, data=dia)


def test_ajuste_de_inventario_exige_motivo_e_lanca_a_diferenca(glicerina, aroma, dia):
    entrada(glicerina, aroma, 1000, "4", dia)
    with pytest.raises(ValidationError, match="motivo"):
        s.ajustar_inventario(produto=glicerina, local=aroma, quantidade_contada_kg=950, data=dia, motivo=" ")
    ajuste = s.ajustar_inventario(produto=glicerina, local=aroma, quantidade_contada_kg=950, data=dia,
                                  motivo="Medição da régua do tanque")
    assert ajuste.quantidade_kg == D("-50")
    assert s.saldo(glicerina, aroma) == D("950")
    assert s.ajustar_inventario(produto=glicerina, local=aroma, quantidade_contada_kg=950, data=dia,
                                motivo="Conferência") is None


def test_lote_repetido_com_custo_diferente_e_recusado(glicerina, aroma, dia):
    entrada(glicerina, aroma, 1000, "4", dia, lote_codigo="X")
    with pytest.raises(ValidationError, match="já existe"):
        entrada(glicerina, aroma, 1000, "5", dia, lote_codigo="X")


def test_tabela_de_saldos(glicerina, aroma, biobrotas, dia):
    entrada(glicerina, aroma, 1000, "4", dia)
    entrada(glicerina, biobrotas, 2000, "5", dia)
    linhas = {l.local.nome: l for l in s.tabela_de_saldos()}
    assert linhas["Tanque Aroma"].quantidade_kg == D("1000")
    assert linhas["Tanque Biobrotas"].valor == D("10000")
    assert linhas["Tanque Biobrotas"].custo_medio == D("5.0000")


def test_conversao_litros_para_kg(glicerina):
    assert glicerina.litros_para_kg(D("5000")) == D("6300.0000")
