from datetime import date
from decimal import Decimal

from apps.integracao import legado as L


def test_documento_monta_cnpj_com_digito():
    assert L.documento(230019640001, 50) == "23001964000150"
    assert L.documento(16158140064, 87) == "01615814006487"
    assert L.documento(0, 0) == ""
    assert L.documento(None, None) == ""


def test_datas_impossiveis_sao_descartadas():
    hoje = date(2026, 11, 14)
    assert L.data_valida(date(1880, 9, 3), hoje) is None
    assert L.data_valida(date(9202, 1, 1), hoje) is None
    assert L.data_valida(date(2028, 8, 24), hoje) is None
    assert L.data_valida(date(2026, 10, 1), hoje) == date(2026, 10, 1)


def test_conversoes():
    assert L.modelo("57") == "57"
    assert L.modelo("1") == "01"
    assert L.modelo("xx") == "99"
    assert L.decimal(234060.0) == Decimal("234060.00")
    assert L.decimal(None) == Decimal("0")
    assert L.cancelada(1) and not L.cancelada(0) and not L.cancelada(None)
    assert L.documento_texto("CTe51261033691357000112") == "51261033691357000112"


def test_situacao_cancelada_e_inutilizada():
    assert L.cancelada(2)
    assert L.SITUACAO_CANCELADA == "*" and L.SITUACAO_DEVOLUCAO == "D"


def test_classificar_produto_pelo_nome():
    assert L.classificar_produto("GLICERINA 80%") == ("GLIC80", L.DENSIDADE_GLICERINA)
    assert L.classificar_produto("GLICERINA BIDESTILADA USP VEGETAL")[0] == "GBD_GRANEL"
    assert L.classificar_produto("GLICERINA BIDESTILADA USP TB PLAS 250KG PROD. ACAB")[0] == "GBD_FRAC"
    assert L.classificar_produto("ROTULO GLICERINA USP 5L")[0] == "EMBALAGEM"
    assert L.classificar_produto("LECITINA DE SOJA - LDC")[0] == "LECITINA"
    assert L.classificar_produto("ETANOL HIDRATADO OUTROS FINS")[0] == "ETANOL"
    assert L.classificar_produto("MATERIAL DE USO E CONSUMO")[0] == "OUTRO"
    assert L.TIPOS_COM_ESTOQUE >= {"GLIC80", "GBD_GRANEL"}


def test_nota_de_devolucao_procura_primeiro_o_fornecedor():
    """No ERP antigo, a NF 4023 (situação D) tem código 523: é o cliente
    Neuripe, mas a nota é para o fornecedor 523, Peter Cremer."""
    from apps.integracao.importador import ImportadorLegado

    imp = ImportadorLegado(cursor=None, empresa=1, desde=date(2026, 1, 1))
    imp._clientes = {523: "Neuripe (cliente)"}
    imp._fornecedores = {523: "Peter Cremer (fornecedor)", 825: "Louis Dreyfus"}
    assert imp._destinatario_saida(523, "D") == "Peter Cremer (fornecedor)"
    assert imp._destinatario_saida(523, "N") == "Neuripe (cliente)"
    assert imp._destinatario_saida(825, "N") == "Louis Dreyfus"
