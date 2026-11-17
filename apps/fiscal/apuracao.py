"""
Conta corrente dos impostos, mês a mês, a partir das notas registradas
(as notas do ERP antigo entram pelo comando importar_legado).

Para cada imposto, em cada mês:

    saldo anterior (crédito que sobrou) + créditos do mês − débitos do mês

Se o resultado é positivo, sobra SALDO CREDOR, que passa para o mês
seguinte. Se é negativo, é o imposto A PAGAR no mês, e o mês seguinte
começa com saldo zero.

- Débitos: notas de saída da JT (CFOP 5, 6 e 7), não canceladas.
- Créditos: notas de entrada e CT-e lançados no livro de entradas.
  As notas de entrada que a própria JT emite (CFOP 1 e 2 no livro de
  saídas, ex.: devolução) já estão no livro de entradas e não são
  somadas duas vezes.

É uma estimativa feita com os livros do sistema antigo: a apuração
oficial é a da contabilidade (SPED Fiscal e EFD-Contribuições), que pode
ter ajustes que não estão nas notas.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth

from .models import NotaFiscal, NotaItem
from .tributos import COFINS, PIS, TRIBUTOS, Tributo

ZERO = Decimal("0")
_DEC = DecimalField(max_digits=18, decimal_places=2)

# Entradas que dão direito a crédito de PIS/COFINS no regime não cumulativo:
# compra para revenda e frete (CT-e) pago pela JT.
CFOP_COMPRA_REVENDA = {"1101", "1102", "2101", "2102", "1403", "2403"}
CFOP_FRETE = {"1351", "1352", "1353", "2351", "2352", "2353", "1932", "2932",
              "5352", "6352", "5353", "6353", "5932", "6932"}


def _itens_validos():
    return NotaItem.objects.filter(nota__cancelada=False).annotate(
        dia=Coalesce("nota__data_entrada", "nota__data_emissao"))


def _itens_debito():
    return _itens_validos().filter(nota__direcao=NotaFiscal.Direcao.SAIDA).exclude(
        Q(cfop__startswith="1") | Q(cfop__startswith="2") | Q(cfop__startswith="3"))


def _itens_credito():
    return _itens_validos().filter(nota__direcao=NotaFiscal.Direcao.ENTRADA)


def _soma_por_mes(qs, campos) -> dict:
    linhas = (qs.annotate(mes=TruncMonth("dia")).values("mes")
              .annotate(**{c: Coalesce(Sum(c), ZERO, output_field=_DEC) for c in campos}).order_by("mes"))
    return {(l["mes"].year, l["mes"].month): l for l in linhas}


@dataclass
class MesImposto:
    ano: int
    mes: int
    saldo_anterior: Decimal = ZERO
    creditos: Decimal = ZERO
    debitos: Decimal = ZERO
    credito_nao_lancado: Decimal = ZERO   # só PIS/COFINS: estimativa do que poderia ter sido creditado

    @property
    def resultado(self) -> Decimal:
        return self.saldo_anterior + self.creditos - self.debitos

    @property
    def a_pagar(self) -> Decimal:
        return -self.resultado if self.resultado < 0 else ZERO

    @property
    def saldo_atual(self) -> Decimal:
        """Saldo credor que passa para o mês seguinte."""
        return self.resultado if self.resultado > 0 else ZERO

    @property
    def data(self) -> date:
        return date(self.ano, self.mes, 1)


@dataclass
class ContaCorrente:
    tributo: Tributo
    meses: list[MesImposto] = field(default_factory=list)

    def do_ano(self, ano: int) -> list[MesImposto]:
        return [m for m in self.meses if m.ano == ano]

    def mes(self, ano: int, mes: int) -> MesImposto | None:
        return next((m for m in self.meses if (m.ano, m.mes) == (ano, mes)), None)


def _meses_entre(inicio: tuple[int, int], fim: tuple[int, int]):
    a, m = inicio
    while (a, m) <= fim:
        yield a, m
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)


def contas_correntes(ate: date | None = None) -> list[ContaCorrente]:
    """Uma conta corrente por imposto, do primeiro mês com notas até `ate`."""
    campos = [t.campo for t in TRIBUTOS]
    debitos = _soma_por_mes(_itens_debito(), campos)
    creditos = _soma_por_mes(_itens_credito(), campos)
    nao_lancado = _soma_por_mes(
        _itens_credito().filter(
            Q(cfop__in=CFOP_COMPRA_REVENDA | CFOP_FRETE) | Q(nota__modelo=NotaFiscal.Modelo.CTE),
            valor_pis=0, valor_cofins=0, valor_total__gt=0,
        ).annotate(base=F("valor_total") - F("valor_icms")),
        ["base"],
    )
    chaves = set(debitos) | set(creditos)
    if not chaves:
        return [ContaCorrente(t) for t in TRIBUTOS]
    fim = max(chaves)
    if ate is not None:
        fim = max(min(fim, (ate.year, ate.month)), min(chaves))
    contas = []
    for t in TRIBUTOS:
        conta, saldo = ContaCorrente(t), ZERO
        for a, m in _meses_entre(min(chaves), fim):
            if date(a, m, 1) < t.vale_desde or (t.vale_ate and date(a, m, 1) > t.vale_ate):
                continue
            mes = MesImposto(
                a, m, saldo_anterior=saldo,
                creditos=(creditos.get((a, m)) or {}).get(t.campo, ZERO),
                debitos=(debitos.get((a, m)) or {}).get(t.campo, ZERO),
            )
            base = (nao_lancado.get((a, m)) or {}).get("base", ZERO)
            if t.codigo == "PIS":
                mes.credito_nao_lancado = base * PIS
            elif t.codigo == "COFINS":
                mes.credito_nao_lancado = base * COFINS
            conta.meses.append(mes)
            saldo = mes.saldo_atual
        contas.append(conta)
    return contas


def icms_por_uf(ano: int, mes: int) -> list[dict]:
    """ICMS do mês por UF: créditos pela UF do fornecedor, débitos pela UF do cliente."""
    def por_uf(qs):
        linhas = (qs.filter(dia__year=ano, dia__month=mes).order_by().values("nota__parceiro__uf")
                  .annotate(icms=Coalesce(Sum("valor_icms"), ZERO, output_field=_DEC),
                            valor=Coalesce(Sum("valor_total"), ZERO, output_field=_DEC)))
        return {(l["nota__parceiro__uf"] or "—"): l for l in linhas}

    cred, deb = por_uf(_itens_credito()), por_uf(_itens_debito())
    ufs = sorted(set(cred) | set(deb))
    resultado = []
    for uf in ufs:
        c, d = cred.get(uf, {}), deb.get(uf, {})
        resultado.append({
            "uf": uf,
            "compras": c.get("valor", ZERO), "credito": c.get("icms", ZERO),
            "vendas": d.get("valor", ZERO), "debito": d.get("icms", ZERO),
        })
    resultado.sort(key=lambda l: -(l["credito"] + l["debito"]))
    return resultado
