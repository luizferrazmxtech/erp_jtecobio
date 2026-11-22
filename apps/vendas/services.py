"""
Regras de venda: saldo livre, sugestão de origens, confirmação (reserva),
faturamento (baixa no estoque) e cancelamento.

Saldo livre de um tanque = saldo em estoque − o que está reservado em
pedidos confirmados e ainda não faturados.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from apps.cadastros.models import Local, Produto
from apps.estoque import services as estoque
from apps.estoque.models import Configuracao, Lote, Movimentacao
from apps.fiscal.models import NotaFiscal
from apps.fiscal.tributos import ICMS_FRETE_PADRAO, Impostos

from . import impostos
from .models import Pedido, PedidoOrigem, TabelaFrete

ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Saldo livre
# ---------------------------------------------------------------------------


def reservado(produto: Produto, local: Local, lote: Lote | None = None, excluir: Pedido | None = None) -> Decimal:
    qs = PedidoOrigem.objects.filter(
        pedido__status=Pedido.Status.CONFIRMADO, pedido__produto=produto, local=local,
    )
    if lote is not None:
        qs = qs.filter(lote=lote)
    if excluir is not None:
        qs = qs.exclude(pedido=excluir)
    return qs.aggregate(t=Sum("quantidade_kg"))["t"] or ZERO


def saldo_livre(produto: Produto, local: Local, lote: Lote | None = None, excluir: Pedido | None = None) -> Decimal:
    return estoque.saldo(produto, local, lote) - reservado(produto, local, lote, excluir)


# ---------------------------------------------------------------------------
# Opções de origem e sugestão
# ---------------------------------------------------------------------------


@dataclass
class Opcao:
    local: Local
    lote: Lote | None
    livre: Decimal
    custo_kg: Decimal
    frete: TabelaFrete | None
    creditos: Impostos = Impostos()
    icms_frete: Decimal = ICMS_FRETE_PADRAO

    @property
    def frete_kg(self) -> Decimal | None:
        return self.frete.preco_kg if self.frete else None

    @property
    def custo_total_kg(self) -> Decimal | None:
        if self.frete is None:
            return None
        return self.custo_kg + self.frete.preco_kg

    @property
    def credito_kg(self) -> Decimal:
        """Crédito de ICMS + PIS + COFINS que veio na compra deste tanque/lote."""
        return self.creditos.total

    @property
    def custo_net_kg(self) -> Decimal:
        """Produto sem impostos: custo − créditos da compra."""
        return self.custo_kg - self.credito_kg

    @property
    def frete_net_kg(self) -> Decimal | None:
        if self.frete is None:
            return None
        return impostos.frete_net(self.frete.preco_kg, self.icms_frete)

    @property
    def custo_total_net_kg(self) -> Decimal | None:
        if self.frete is None:
            return None
        return self.custo_net_kg + self.frete_net_kg

    def chave_ordem(self):
        # O que conta é o custo net: o imposto pago na compra volta como crédito.
        # Origens sem frete cadastrado vão para o fim da fila.
        return (self.frete is None, self.custo_total_net_kg or self.custo_net_kg, -self.livre)


def opcoes_de_origem(produto: Produto, cidade: str, uf: str, excluir: Pedido | None = None,
                     icms_frete=ICMS_FRETE_PADRAO) -> list[Opcao]:
    """Todos os tanques com saldo livre do produto, com custo, créditos e frete até o destino.

    Cada tanque usa o crédito real que veio nas suas compras: um tanque
    abastecido com compra de dentro de SP (ICMS 18%) tem custo net menor
    que outro com o mesmo preço comprado de fora (12%).
    Locais de produto de cliente (serviço de envase) nunca entram numa venda.
    """
    por_lote = Configuracao.atual().metodo_custo == Configuracao.MetodoCusto.LOTE
    opcoes = []
    locais = Local.objects.filter(
        ativo=True, movimentacoes__produto=produto,
    ).exclude(tipo=Local.Tipo.TERCEIROS).distinct()
    for local in locais:
        frete = TabelaFrete.buscar(local, cidade, uf)
        if por_lote:
            lotes = Lote.objects.filter(produto=produto, movimentacoes__local=local).distinct()
            for lote in lotes:
                livre = saldo_livre(produto, local, lote, excluir)
                if livre > 0:
                    opcoes.append(Opcao(local, lote, livre, lote.custo_unitario, frete, lote.creditos_kg, icms_frete))
        else:
            livre = saldo_livre(produto, local, None, excluir)
            if livre > 0:
                opcoes.append(Opcao(local, None, livre, estoque.custo_medio(produto, local), frete,
                                    estoque.creditos_medios(produto, local), icms_frete))
    opcoes.sort(key=Opcao.chave_ordem)
    return opcoes


@dataclass
class Sugestao:
    origens: list[tuple[Opcao, Decimal]]
    faltante: Decimal

    @property
    def custo_total(self) -> Decimal:
        return sum(
            (q * (o.custo_kg + (o.frete_kg or ZERO)) for o, q in self.origens), ZERO
        )


def sugerir_origens(
    produto: Produto, quantidade_kg, cidade: str, uf: str,
    minimo_por_origem_kg=0, excluir: Pedido | None = None, icms_frete=ICMS_FRETE_PADRAO,
) -> Sugestao:
    """Escolhe as origens de menor custo efetivo net (produto + frete, sem impostos).

    Vai pegando do tanque mais barato até completar a quantidade. Um tanque
    que só completaria menos que `minimo_por_origem_kg` é pulado (não vale
    mandar um caminhão buscar pouco), a não ser que seja o que falta.
    """
    restante = Decimal(str(quantidade_kg))
    minimo = Decimal(str(minimo_por_origem_kg or 0))
    escolhidas = []
    for opcao in opcoes_de_origem(produto, cidade, uf, excluir, icms_frete):
        if restante <= 0:
            break
        pegar = min(opcao.livre, restante)
        if pegar < minimo and pegar < restante:
            continue
        escolhidas.append((opcao, pegar))
        restante -= pegar
    return Sugestao(escolhidas, max(restante, ZERO))


# ---------------------------------------------------------------------------
# Ciclo do pedido
# ---------------------------------------------------------------------------


def _travar(pedido: Pedido) -> Pedido:
    """Relê o pedido do banco travando a linha: evita agir sobre uma cópia
    desatualizada e impede duas pessoas mudarem o mesmo pedido ao mesmo tempo."""
    return Pedido.objects.select_for_update().get(pk=pedido.pk)


def _exigir_status(pedido: Pedido, *status):
    if pedido.status not in status:
        nomes = ", ".join(Pedido.Status(s).label for s in status)
        raise ValidationError(f"O pedido {pedido.numero} está {pedido.get_status_display().lower()}; precisa estar: {nomes}.")


@transaction.atomic
def definir_origens(pedido: Pedido, origens: list[dict]) -> None:
    """Substitui as origens de um pedido em rascunho.

    Cada origem: {"local", "quantidade_kg", "lote"?, "frete_kg"?, "transportadora"?}.
    Se o frete não for informado, usa o da tabela de frete.
    """
    _exigir_status(_travar(pedido), Pedido.Status.RASCUNHO)
    total = sum((Decimal(str(o["quantidade_kg"])) for o in origens), ZERO)
    if total > pedido.quantidade_kg:
        raise ValidationError(
            f"Os tanques somam {total:.0f} kg, mais que os {pedido.quantidade_kg:.0f} kg do pedido."
        )
    pedido.origens.all().delete()
    por_lote = Configuracao.atual().metodo_custo == Configuracao.MetodoCusto.LOTE
    for o in origens:
        local, lote = o["local"], o.get("lote")
        quantidade = Decimal(str(o["quantidade_kg"]))
        if quantidade <= 0:
            continue
        if local.tipo == Local.Tipo.TERCEIROS:
            raise ValidationError(f"{local} guarda produto de cliente e não pode ser origem de venda.")
        if lote is not None and lote.produto_id != pedido.produto_id:
            raise ValidationError(f"O lote {lote.codigo} é de outro produto.")
        frete_kg = o.get("frete_kg")
        transportadora = o.get("transportadora")
        if frete_kg is None:
            tabela = TabelaFrete.buscar(local, pedido.destino_cidade, pedido.destino_uf)
            frete_kg = tabela.preco_kg if tabela else ZERO
            transportadora = transportadora or (tabela.transportadora if tabela else None)
        if por_lote and lote is not None:
            custo, creditos = lote.custo_unitario, lote.creditos_kg
        else:
            custo = estoque.custo_medio(pedido.produto, local)
            creditos = estoque.creditos_medios(pedido.produto, local)
        origem = PedidoOrigem(
            pedido=pedido, local=local, lote=lote, quantidade_kg=quantidade,
            custo_unitario=custo, frete_kg=frete_kg, transportadora=transportadora,
        )
        origem.definir_creditos(creditos)
        origem.save()


def aplicar_sugestao(pedido: Pedido, minimo_por_origem_kg=0) -> Sugestao:
    sugestao = sugerir_origens(
        pedido.produto, pedido.quantidade_kg, pedido.destino_cidade, pedido.destino_uf,
        minimo_por_origem_kg, excluir=pedido, icms_frete=pedido.icms_frete,
    )
    definir_origens(pedido, [
        {"local": o.local, "lote": o.lote, "quantidade_kg": q,
         "frete_kg": o.frete_kg, "transportadora": o.frete.transportadora if o.frete else None}
        for o, q in sugestao.origens
    ])
    return sugestao


@transaction.atomic
def confirmar(pedido: Pedido) -> None:
    """Reserva as quantidades nos tanques."""
    original, pedido = pedido, _travar(pedido)
    _exigir_status(pedido, Pedido.Status.RASCUNHO)
    origens = list(pedido.origens.select_related("local", "lote"))
    if not origens:
        raise ValidationError("Escolha pelo menos uma origem antes de confirmar.")
    alocado = sum((o.quantidade_kg for o in origens), ZERO)
    if alocado != pedido.quantidade_kg:
        raise ValidationError(
            f"As origens somam {alocado:.3f} kg, mas o pedido é de {pedido.quantidade_kg:.3f} kg."
        )
    if pedido.contrato_id:
        c = pedido.contrato
        if c.parceiro_id != pedido.cliente_id or c.produto_id != pedido.produto_id:
            raise ValidationError("O contrato escolhido é de outro cliente ou de outro produto.")
    por_lote = Configuracao.atual().metodo_custo == Configuracao.MetodoCusto.LOTE
    for o in origens:
        if por_lote and o.lote is None:
            raise ValidationError(f"Com custo por lote, informe o lote da origem {o.local}.")
        Local.objects.select_for_update().filter(pk=o.local_id).first()
        livre = saldo_livre(pedido.produto, o.local, o.lote, excluir=pedido)
        if o.quantidade_kg > livre:
            raise ValidationError(
                f"{o.local}: saldo livre {livre:.3f} kg, o pedido pede {o.quantidade_kg:.3f} kg."
            )
    pedido.status = Pedido.Status.CONFIRMADO
    pedido.save(update_fields=["status"])
    original.refresh_from_db()


@transaction.atomic
def voltar_para_rascunho(pedido: Pedido) -> None:
    """Libera a reserva para poder mudar as origens."""
    original, pedido = pedido, _travar(pedido)
    _exigir_status(pedido, Pedido.Status.CONFIRMADO)
    pedido.status = Pedido.Status.RASCUNHO
    pedido.save(update_fields=["status"])
    original.refresh_from_db()


@transaction.atomic
def faturar(pedido: Pedido, data: date, nota: NotaFiscal | None = None, usuario=None) -> list[Movimentacao]:
    """Baixa o estoque de cada origem e registra o custo real."""
    original, pedido = pedido, _travar(pedido)
    _exigir_status(pedido, Pedido.Status.CONFIRMADO)
    if nota is not None and nota.direcao != NotaFiscal.Direcao.SAIDA:
        raise ValidationError("A nota do faturamento precisa ser uma nota de saída.")
    documento = f"Pedido {pedido.numero}" + (f" / NF {nota.numero}" if nota else "")
    movimentos = []
    for o in pedido.origens.select_related("local", "lote"):
        mov = estoque.registrar_saida(
            produto=pedido.produto, local=o.local, quantidade_kg=o.quantidade_kg, data=data,
            lote=o.lote, nota=nota, contrato=pedido.contrato, documento=documento,
            observacao=f"Venda para {pedido.cliente}", usuario=usuario,
        )
        o.movimentacao = mov
        o.custo_unitario = mov.custo_unitario
        o.definir_creditos(mov.creditos_kg)
        o.save(update_fields=["movimentacao", "custo_unitario", "credito_icms_kg", "credito_pis_kg",
                              "credito_cofins_kg"])
        movimentos.append(mov)
    pedido.status = Pedido.Status.FATURADO
    pedido.nota = nota
    pedido.data_faturamento = data
    pedido.save(update_fields=["status", "nota", "data_faturamento"])
    original.refresh_from_db()
    return movimentos


@transaction.atomic
def cancelar(pedido: Pedido) -> None:
    original, pedido = pedido, _travar(pedido)
    _exigir_status(pedido, Pedido.Status.RASCUNHO, Pedido.Status.CONFIRMADO)
    pedido.status = Pedido.Status.CANCELADO
    pedido.save(update_fields=["status"])
    original.refresh_from_db()
