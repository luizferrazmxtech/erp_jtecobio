from datetime import date
from decimal import Decimal

from apps.cadastros.models import Parceiro
from apps.contratos.models import Contrato
from apps.fiscal.models import NotaFiscal, NotaItem


def _nota(parceiro, numero, kg, contrato, cancelada=False):
    nota = NotaFiscal.objects.create(direcao="S", numero=numero, serie="1", parceiro=parceiro,
                                     data_emissao=date(2026, 10, 8), cancelada=cancelada)
    NotaItem.objects.create(nota=nota, item=1, descricao="Glicerina", quantidade=kg, contrato=contrato)


def test_saldo_do_contrato_desconta_notas_e_ignora_canceladas(glicerina):
    aroma = Parceiro.objects.create(razao_social="Aroma Bioquímica", eh_cliente=True)
    c = Contrato.objects.create(numero="JT2309/2026", tipo="V", parceiro=aroma, produto=glicerina,
                                quantidade_kg=300000, preco_unitario=Decimal("4.70"),
                                mes_referencia=date(2026, 10, 1))
    _nota(aroma, 3852, 49800, c)
    _nota(aroma, 3861, 49100, c)
    _nota(aroma, 3837, 10000, c, cancelada=True)
    assert c.calcular_saldo() == Decimal("201100")
    anotado = Contrato.objects.com_saldo().get(pk=c.pk)
    assert anotado.quantidade_entregue == Decimal("98900")
    assert anotado.saldo == Decimal("201100")
    assert c.valor_total == Decimal("1410000")
