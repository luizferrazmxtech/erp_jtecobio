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
