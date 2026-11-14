"""
Regras de estoque. Toda movimentação deve ser criada por estas funções,
que validam saldo, calculam custo e gravam quem fez.

Convenção: quantidades em kg, sempre positivas nos parâmetros; o sinal é
decidido aqui (entradas positivas, saídas negativas).
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, F, Sum
from django.db.models.functions import Coalesce

from apps.cadastros.models import Local, Produto

from .models import Configuracao, Lote, Movimentacao

ZERO = Decimal("0")
QUATRO_CASAS = Decimal("0.0001")


def _dec(valor) -> Decimal:
    return valor if isinstance(valor, Decimal) else Decimal(str(valor))


def _arredondar_custo(valor: Decimal) -> Decimal:
    return valor.quantize(QUATRO_CASAS, rounding=ROUND_HALF_UP)


def _movs(produto, local, lote=None, ate: date | None = None):
    qs = Movimentacao.objects.filter(produto=produto, local=local)
    if lote is not None:
        qs = qs.filter(lote=lote)
    if ate is not None:
        qs = qs.filter(data__lte=ate)
    return qs


_DEC = DecimalField(max_digits=20, decimal_places=4)


def saldo(produto: Produto, local: Local, lote: Lote | None = None, ate: date | None = None) -> Decimal:
    """Quantidade em kg do produto no local (opcionalmente de um lote, até uma data)."""
    total = _movs(produto, local, lote, ate).aggregate(t=Sum("quantidade_kg"))["t"]
    return total or ZERO


def valor_em_estoque(produto: Produto, local: Local, lote: Lote | None = None) -> Decimal:
    total = _movs(produto, local, lote).aggregate(
        v=Coalesce(Sum(F("quantidade_kg") * F("custo_unitario"), output_field=_DEC), ZERO, output_field=_DEC)
    )["v"]
    return total or ZERO


def custo_medio(produto: Produto, local: Local) -> Decimal:
    """Custo médio ponderado (R$/kg) do produto no local.

    Como as saídas são lançadas ao custo médio do momento, valor total ÷
    quantidade total é sempre o custo médio corrente.
    """
    qtd = saldo(produto, local)
    if qtd <= 0:
        return ZERO
    return _arredondar_custo(valor_em_estoque(produto, local) / qtd)


@dataclass
class LinhaSaldo:
    produto: Produto
    local: Local
    quantidade_kg: Decimal
    valor: Decimal

    @property
    def custo_medio(self) -> Decimal:
        if not self.quantidade_kg:
            return ZERO
        return _arredondar_custo(self.valor / self.quantidade_kg)


def tabela_de_saldos(incluir_zerados: bool = False, produto=None, local=None) -> list[LinhaSaldo]:
    """Saldo e valor por produto × local, para relatórios."""
    qs = Movimentacao.objects.all()
    if produto:
        qs = qs.filter(produto=produto)
    if local:
        qs = qs.filter(local=local)
    linhas = (
        qs.values("produto", "local")
        .annotate(
            qtd=Sum("quantidade_kg"),
            valor=Sum(F("quantidade_kg") * F("custo_unitario"), output_field=_DEC),
        )
        .order_by()
    )
    produtos = {p.pk: p for p in Produto.objects.all()}
    locais = {l.pk: l for l in Local.objects.all()}
    resultado = []
    for l in linhas:
        if not incluir_zerados and not l["qtd"]:
            continue
        resultado.append(
            LinhaSaldo(produtos[l["produto"]], locais[l["local"]], l["qtd"] or ZERO, l["valor"] or ZERO)
        )
    resultado.sort(key=lambda x: (x.produto.nome, x.local.nome))
    return resultado


# ---------------------------------------------------------------------------
# Lançamentos
# ---------------------------------------------------------------------------


def _travar_local(local: Local) -> None:
    """Evita que duas saídas simultâneas do mesmo local passem do saldo."""
    Local.objects.select_for_update().filter(pk=local.pk).first()


def _validar_quantidade(quantidade) -> Decimal:
    q = _dec(quantidade)
    if q <= 0:
        raise ValidationError("A quantidade deve ser maior que zero.")
    return q


def _custo_de_saida(produto, local, lote) -> Decimal:
    config = Configuracao.atual()
    if config.metodo_custo == Configuracao.MetodoCusto.LOTE:
        if lote is None:
            raise ValidationError("Com custo por lote, informe de qual lote o produto sai.")
        return lote.custo_unitario
    return custo_medio(produto, local)


def _validar_saldo(produto, local, lote, quantidade: Decimal) -> None:
    if Configuracao.atual().permitir_saldo_negativo:
        return
    disponivel = saldo(produto, local, lote)
    if quantidade > disponivel:
        onde = f"{local}" + (f", lote {lote.codigo}" if lote else "")
        raise ValidationError(
            f"Saldo insuficiente de {produto} em {onde}: disponível {disponivel:.3f} kg, "
            f"pedido {quantidade:.3f} kg."
        )


def _validar_lote(produto, lote):
    if lote is not None and lote.produto_id != produto.pk:
        raise ValidationError("O lote informado é de outro produto.")


def _codigo_automatico(produto, nota, tipo, data, local, custo) -> str:
    """Gera um código de lote. Reaproveita o lote se o custo for o mesmo
    (ex.: uma NF dividida em dois tanques); senão acrescenta um sufixo."""
    base = f"NF{nota.numero}-{nota.parceiro_id}" if nota is not None else f"{tipo}-{data:%Y%m%d}-L{local.pk}"
    codigo, n = base, 1
    while True:
        existente = Lote.objects.filter(produto=produto, codigo=codigo).first()
        if existente is None or existente.custo_unitario == custo:
            return codigo
        n += 1
        codigo = f"{base}-{n}"


@transaction.atomic
def registrar_entrada(
    *,
    produto: Produto,
    local: Local,
    quantidade_kg,
    custo_unitario,
    data: date,
    lote_codigo: str = "",
    tipo: str = Movimentacao.Tipo.ENTRADA,
    nota=None,
    contrato=None,
    documento: str = "",
    observacao: str = "",
    usuario=None,
    origem_lote: str = "",
) -> Movimentacao:
    """Entrada de produto (compra, saldo inicial, retorno de industrialização).

    Sempre cria (ou reaproveita) um lote, para que o custo por lote funcione
    mesmo que o cliente comece usando custo médio.
    """
    quantidade = _validar_quantidade(quantidade_kg)
    custo = _dec(custo_unitario)
    if custo < 0:
        raise ValidationError("O custo não pode ser negativo.")
    if not lote_codigo:
        lote_codigo = _codigo_automatico(produto, nota, tipo, data, local, custo)
    lote, criado = Lote.objects.get_or_create(
        produto=produto,
        codigo=lote_codigo,
        defaults={"data": data, "custo_unitario": custo, "nota_entrada": nota, "origem": origem_lote},
    )
    if not criado and lote.custo_unitario != custo:
        raise ValidationError(
            f"O lote {lote_codigo} já existe com custo {lote.custo_unitario}. Use outro código de lote."
        )
    return Movimentacao.objects.create(
        data=data, tipo=tipo, produto=produto, local=local, lote=lote,
        quantidade_kg=quantidade, custo_unitario=custo, nota=nota, contrato=contrato,
        documento=documento, observacao=observacao, usuario=usuario,
    )


@transaction.atomic
def registrar_saida(
    *,
    produto: Produto,
    local: Local,
    quantidade_kg,
    data: date,
    lote: Lote | None = None,
    tipo: str = Movimentacao.Tipo.SAIDA,
    nota=None,
    contrato=None,
    documento: str = "",
    observacao: str = "",
    usuario=None,
) -> Movimentacao:
    """Saída de produto (venda, remessa, perda). Valida saldo e calcula custo."""
    quantidade = _validar_quantidade(quantidade_kg)
    _validar_lote(produto, lote)
    _travar_local(local)
    _validar_saldo(produto, local, lote, quantidade)
    custo = _custo_de_saida(produto, local, lote)
    return Movimentacao.objects.create(
        data=data, tipo=tipo, produto=produto, local=local, lote=lote,
        quantidade_kg=-quantidade, custo_unitario=custo, nota=nota, contrato=contrato,
        documento=documento, observacao=observacao, usuario=usuario,
    )


@transaction.atomic
def transferir(
    *,
    produto: Produto,
    origem: Local,
    destino: Local,
    quantidade_kg,
    data: date,
    lote: Lote | None = None,
    documento: str = "",
    observacao: str = "",
    usuario=None,
) -> tuple[Movimentacao, Movimentacao]:
    """Move produto de um local para outro mantendo o custo."""
    if origem.pk == destino.pk:
        raise ValidationError("Origem e destino são o mesmo local.")
    saida = registrar_saida(
        produto=produto, local=origem, quantidade_kg=quantidade_kg, data=data, lote=lote,
        tipo=Movimentacao.Tipo.TRANSF_SAIDA, documento=documento, observacao=observacao, usuario=usuario,
    )
    entrada = Movimentacao.objects.create(
        data=data, tipo=Movimentacao.Tipo.TRANSF_ENTRADA, produto=produto, local=destino,
        lote=saida.lote, quantidade_kg=-saida.quantidade_kg, custo_unitario=saida.custo_unitario,
        documento=documento, observacao=observacao, usuario=usuario, par=saida,
    )
    saida.par = entrada
    saida.save(update_fields=["par"])
    return saida, entrada


@transaction.atomic
def ajustar_inventario(
    *,
    produto: Produto,
    local: Local,
    quantidade_contada_kg,
    data: date,
    motivo: str,
    lote: Lote | None = None,
    usuario=None,
) -> Movimentacao | None:
    """Corrige o saldo para a quantidade contada. Motivo é obrigatório.

    Retorna None quando não há diferença.
    """
    if not motivo or not motivo.strip():
        raise ValidationError("Informe o motivo do ajuste de inventário.")
    contada = _dec(quantidade_contada_kg)
    if contada < 0:
        raise ValidationError("A quantidade contada não pode ser negativa.")
    _validar_lote(produto, lote)
    _travar_local(local)
    atual = saldo(produto, local, lote)
    diferenca = contada - atual
    if diferenca == 0:
        return None
    if diferenca > 0:
        custo = lote.custo_unitario if lote else custo_medio(produto, local)
    else:
        custo = _custo_de_saida(produto, local, lote)
    return Movimentacao.objects.create(
        data=data, tipo=Movimentacao.Tipo.AJUSTE, produto=produto, local=local, lote=lote,
        quantidade_kg=diferenca, custo_unitario=custo, observacao=motivo.strip(), usuario=usuario,
        documento=f"Inventário: contado {contada} kg, sistema {atual} kg",
    )
