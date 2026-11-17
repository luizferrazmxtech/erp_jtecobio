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
from apps.fiscal import tributos
from apps.fiscal.tributos import Impostos

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


def creditos_medios(produto: Produto, local: Local, lote: Lote | None = None) -> Impostos:
    """Crédito médio por kg (ICMS, PIS, COFINS) do que está no tanque.

    Funciona como o custo médio: cada entrada traz o crédito da sua nota e
    cada saída leva o crédito médio do momento.
    """
    qtd = saldo(produto, local, lote)
    if qtd <= 0:
        return Impostos()
    somas = _movs(produto, local, lote).aggregate(**{
        nome: Coalesce(Sum(F("quantidade_kg") * F(f"credito_{nome}_kg"), output_field=_DEC), ZERO, output_field=_DEC)
        for nome in ("icms", "pis", "cofins")
    })
    return Impostos(somas["icms"], somas["pis"], somas["cofins"]).dividido(qtd).arredondado(tributos.SEIS_CASAS)


def creditos_da_nota(nota, produto: Produto) -> Impostos | None:
    """Crédito por kg tirado da própria nota de compra (valores destacados nela).

    Retorna None quando a nota não tem o produto ou não tem impostos lançados.
    """
    if nota is None:
        return None
    item = nota.itens.filter(produto=produto, quantidade__gt=0).first()
    if item is None or not (item.valor_icms or item.valor_pis or item.valor_cofins):
        return None
    return Impostos(item.valor_icms, item.valor_pis, item.valor_cofins).dividido(item.quantidade).arredondado(
        tributos.SEIS_CASAS)


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


def _creditos_de_saida(produto, local, lote) -> Impostos:
    if Configuracao.atual().metodo_custo == Configuracao.MetodoCusto.LOTE and lote is not None:
        return lote.creditos_kg
    return creditos_medios(produto, local)


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
    icms_pct=None,
    creditos: Impostos | None = None,
) -> Movimentacao:
    """Entrada de produto (compra, saldo inicial, retorno de industrialização).

    Sempre cria (ou reaproveita) um lote, para que o custo por lote funcione
    mesmo que o cliente comece usando custo médio.

    Créditos de imposto por kg, nesta ordem: os informados em `creditos`;
    os destacados na nota de compra; ou calculados sobre o custo com o ICMS
    `icms_pct` (em branco: o ICMS de compra do cadastro do produto).
    """
    quantidade = _validar_quantidade(quantidade_kg)
    custo = _dec(custo_unitario)
    if custo < 0:
        raise ValidationError("O custo não pode ser negativo.")
    if icms_pct is None or icms_pct == "":
        icms_pct = produto.icms_compra
    if creditos is None:
        creditos = creditos_da_nota(nota, produto) or tributos.credito_por_kg(custo, icms_pct)
    if creditos.total > custo:
        raise ValidationError("Os créditos de imposto não podem ser maiores que o custo.")
    if not lote_codigo:
        lote_codigo = _codigo_automatico(produto, nota, tipo, data, local, custo)
    lote, criado = Lote.objects.get_or_create(
        produto=produto,
        codigo=lote_codigo,
        defaults={"data": data, "custo_unitario": custo, "nota_entrada": nota, "origem": origem_lote,
                  "icms_compra": icms_pct, "credito_icms_kg": creditos.icms,
                  "credito_pis_kg": creditos.pis, "credito_cofins_kg": creditos.cofins},
    )
    if not criado and lote.custo_unitario != custo:
        raise ValidationError(
            f"O lote {lote_codigo} já existe com custo {lote.custo_unitario}. Use outro código de lote."
        )
    return Movimentacao.objects.create(
        data=data, tipo=tipo, produto=produto, local=local, lote=lote,
        quantidade_kg=quantidade, custo_unitario=custo, nota=nota, contrato=contrato,
        documento=documento, observacao=observacao, usuario=usuario,
        credito_icms_kg=creditos.icms, credito_pis_kg=creditos.pis, credito_cofins_kg=creditos.cofins,
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
    cred = _creditos_de_saida(produto, local, lote)
    return Movimentacao.objects.create(
        data=data, tipo=tipo, produto=produto, local=local, lote=lote,
        quantidade_kg=-quantidade, custo_unitario=custo, nota=nota, contrato=contrato,
        documento=documento, observacao=observacao, usuario=usuario,
        credito_icms_kg=cred.icms, credito_pis_kg=cred.pis, credito_cofins_kg=cred.cofins,
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
        credito_icms_kg=saida.credito_icms_kg, credito_pis_kg=saida.credito_pis_kg,
        credito_cofins_kg=saida.credito_cofins_kg,
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
        cred = lote.creditos_kg if lote else creditos_medios(produto, local)
    else:
        custo = _custo_de_saida(produto, local, lote)
        cred = _creditos_de_saida(produto, local, lote)
    return Movimentacao.objects.create(
        data=data, tipo=Movimentacao.Tipo.AJUSTE, produto=produto, local=local, lote=lote,
        quantidade_kg=diferenca, custo_unitario=custo, observacao=motivo.strip(), usuario=usuario,
        credito_icms_kg=cred.icms, credito_pis_kg=cred.pis, credito_cofins_kg=cred.cofins,
        documento=f"Inventário: contado {contada} kg, sistema {atual} kg",
    )
