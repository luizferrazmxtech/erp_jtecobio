"""Custo net (sem impostos) x custo com impostos.

As regras completas (crédito por imposto, frete, exportação) ficam em
`apps/fiscal/tributos.py`; aqui ficam os atalhos em forma de fator.

A JT compra e vende com ICMS, PIS e COFINS. O que ela paga de imposto na
compra volta como crédito, então o custo que importa para a margem é o
"net": o preço sem esses impostos. A planilha faz assim:

    fator = (1 − ICMS) × (1 − PIS − COFINS)
    ICMS 12% → 0,88 × 0,9075 = 0,7986   (compra de glicerina, venda para fora de SP)
    ICMS 18% → 0,82 × 0,9075 = 0,74415  (venda dentro de SP)

    preço net = preço bruto × fator
    frete net = frete × 0,7986          (CT-e com ICMS 12%: crédito de ICMS e
                                         também de PIS/COFINS sobre o resto)

Obs.: a planilha DRE usa frete × 0,88 (só o ICMS). O crédito de PIS/COFINS
do frete pago pela JT também é permitido, então o sistema o considera.

Exportação não tem ICMS, PIS nem COFINS: o fator é 1.
"""

from decimal import ROUND_HALF_UP, Decimal

PIS = Decimal("0.0165")
COFINS = Decimal("0.076")
ICMS_FRETE = Decimal("0.12")
UM = Decimal("1")


def fator(icms_pct, pis_cofins: bool = True) -> Decimal:
    """Quanto sobra do preço depois de tirar os impostos que viram crédito/débito."""
    icms = Decimal(str(icms_pct or 0)) / 100
    resto = UM - (PIS + COFINS) if pis_cofins else UM
    return ((UM - icms) * resto).quantize(Decimal("0.000001"), ROUND_HALF_UP)


def frete_net(frete_kg, icms_frete_pct=12) -> Decimal:
    """Frete sem os créditos de ICMS, PIS e COFINS do CT-e."""
    from apps.fiscal.tributos import credito_frete

    valor = Decimal(str(frete_kg or 0))
    return valor - credito_frete(valor, icms_frete_pct).total


def icms_venda_padrao(uf_destino: str) -> Decimal:
    """ICMS de venda saindo de SP: 18% dentro do estado, 12% para fora.

    É só o valor inicial do pedido; quem vende pode mudar (ex.: 7% para
    alguns estados, 4% para produto importado)."""
    uf = (uf_destino or "").upper().strip()
    if uf == "EX":
        return Decimal("0")
    return Decimal("18") if uf in ("SP", "") else Decimal("12")
