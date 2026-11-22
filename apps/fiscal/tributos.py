"""
Regras dos impostos que a JT recupera (regime não cumulativo, Lucro Real).

A ideia em uma frase: o imposto pago na compra vira CRÉDITO, o imposto da
venda é DÉBITO, e a JT só paga a diferença, apurada no mês inteiro.

Regras usadas aqui (as mesmas da lei, sem simplificar):

    ICMS        = valor × alíquota do ICMS
    base PIS/COFINS = valor − ICMS          (o ICMS sai da base, na venda e
                                             na compra; Lei 14.592/2023)
    PIS         = base × 1,65%
    COFINS      = base × 7,6%

Isso vale igual para:
  - o crédito da compra de mercadoria para revenda (ICMS 12% de fora de SP,
    18% dentro de SP);
  - o crédito do frete (CT-e) da compra e da venda pago pela JT;
  - o débito da venda (18% dentro de SP, 12% para outros estados).

Exportação (e remessa com fim de exportação) não tem débito de ICMS, PIS
nem COFINS, mas a JT continua com o crédito da compra.

Reforma tributária: a partir de 2027 a CBS substitui o PIS e a COFINS, e o
IBS entra aos poucos no lugar do ICMS (2029 a 2033). A conta corrente
(`apps/fiscal/apuracao.py`) percorre a lista TRIBUTOS: para incluir CBS e
IBS basta acrescentar os campos nas notas e uma linha nessa lista, com a
data em que passam a valer.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
PIS = Decimal("0.0165")
COFINS = Decimal("0.076")
ICMS_FRETE_PADRAO = Decimal("12")

CENTAVO = Decimal("0.01")
SEIS_CASAS = Decimal("0.000001")


def _dec(valor) -> Decimal:
    if valor is None:
        return ZERO
    return valor if isinstance(valor, Decimal) else Decimal(str(valor))


@dataclass(frozen=True)
class Impostos:
    """ICMS, PIS e COFINS de um valor (pode ser um crédito ou um débito)."""

    icms: Decimal = ZERO
    pis: Decimal = ZERO
    cofins: Decimal = ZERO

    @property
    def pis_cofins(self) -> Decimal:
        return self.pis + self.cofins

    @property
    def total(self) -> Decimal:
        return self.icms + self.pis + self.cofins

    def __add__(self, outro: "Impostos") -> "Impostos":
        return Impostos(self.icms + outro.icms, self.pis + outro.pis, self.cofins + outro.cofins)

    def __sub__(self, outro: "Impostos") -> "Impostos":
        return Impostos(self.icms - outro.icms, self.pis - outro.pis, self.cofins - outro.cofins)

    def vezes(self, fator) -> "Impostos":
        f = _dec(fator)
        return Impostos(self.icms * f, self.pis * f, self.cofins * f)

    def dividido(self, divisor) -> "Impostos":
        d = _dec(divisor)
        if not d:
            return Impostos()
        return Impostos(self.icms / d, self.pis / d, self.cofins / d)

    def arredondado(self, casas: Decimal = CENTAVO) -> "Impostos":
        return Impostos(*(v.quantize(casas, ROUND_HALF_UP) for v in (self.icms, self.pis, self.cofins)))

    def como_dict(self) -> dict:
        return {"icms": self.icms, "pis": self.pis, "cofins": self.cofins}


def sobre_valor(valor, icms_pct, pis_cofins: bool = True) -> Impostos:
    """ICMS, PIS e COFINS contidos num valor de nota (crédito ou débito).

    Exemplo: compra de 10.000 kg a R$ 5 com ICMS 12% (R$ 50.000):
    ICMS 6.000; base 44.000; PIS 726; COFINS 3.344.
    """
    v = _dec(valor)
    icms = v * _dec(icms_pct) / 100
    if not pis_cofins:
        return Impostos(icms=icms)
    base = v - icms
    return Impostos(icms=icms, pis=base * PIS, cofins=base * COFINS)


def credito_por_kg(custo_kg, icms_pct) -> Impostos:
    """Crédito por kg de uma compra para revenda, com 6 casas (vai para o lote)."""
    return sobre_valor(custo_kg, icms_pct).arredondado(SEIS_CASAS)


def credito_frete(valor, icms_pct=ICMS_FRETE_PADRAO) -> Impostos:
    """Crédito do frete pago pela JT (CT-e): ICMS destacado + PIS/COFINS sobre o resto."""
    return sobre_valor(valor, icms_pct)


def debito_venda(valor, icms_pct, exportacao: bool = False) -> Impostos:
    if exportacao:
        return Impostos()
    return sobre_valor(valor, icms_pct)


# ---------------------------------------------------------------------------
# Tributos da conta corrente
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tributo:
    codigo: str
    nome: str
    campo: str            # campo de valor em NotaItem
    vale_desde: date
    vale_ate: date | None = None


TRIBUTOS = [
    Tributo("ICMS", "ICMS", "valor_icms", date(1900, 1, 1)),
    # Em 2027, quando a CBS substituir PIS e COFINS: vale_ate=date(2026, 12, 31)
    # nestas duas linhas e Tributo("CBS", "CBS", "valor_cbs", date(2027, 1, 1)).
    Tributo("PIS", "PIS", "valor_pis", date(1900, 1, 1)),
    Tributo("COFINS", "COFINS", "valor_cofins", date(1900, 1, 1)),
    # 2029 em diante: Tributo("IBS", "IBS", "valor_ibs", date(2029, 1, 1)),
]
