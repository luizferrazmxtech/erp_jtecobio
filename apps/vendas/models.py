"""
Vendas: pedido de venda que pode ser atendido por vários tanques.

Fluxo de um pedido:
  RASCUNHO   → escolhe as origens (o sistema sugere a mais barata)
  CONFIRMADO → as quantidades ficam reservadas nos tanques
  FATURADO   → baixa no estoque (uma saída por origem), com a NF
  CANCELADO  → libera a reserva (só antes de faturar)

Cada pedido é de um único produto, como as notas da JT.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Sum

from apps.cadastros.models import Local, Parceiro, Produto
from apps.contratos.models import Contrato
from apps.estoque.models import CreditosPorKg, Lote, Movimentacao
from apps.fiscal.models import NotaFiscal

from apps.fiscal import tributos
from apps.fiscal.tributos import Impostos

from . import impostos

ZERO = Decimal("0")


class TabelaFrete(models.Model):
    """Preço de frete (R$/kg) de um local de origem até um destino.

    O destino pode ser uma cidade (mais específico) ou só a UF. Na busca,
    a cidade tem prioridade sobre a UF.
    """

    origem = models.ForeignKey(Local, on_delete=models.CASCADE, related_name="fretes")
    destino_uf = models.CharField("UF de destino", max_length=2)
    destino_cidade = models.CharField(
        "cidade de destino", max_length=100, blank=True,
        help_text="Deixe em branco para valer para a UF inteira.",
    )
    preco_kg = models.DecimalField("frete (R$/kg)", max_digits=8, decimal_places=4)
    transportadora = models.ForeignKey(
        Parceiro, on_delete=models.SET_NULL, null=True, blank=True,
        limit_choices_to={"eh_transportadora": True},
    )
    atualizado_em = models.DateField(auto_now=True)

    class Meta:
        ordering = ["origem", "destino_uf", "destino_cidade"]
        verbose_name = "frete"
        verbose_name_plural = "tabela de frete"
        constraints = [
            models.UniqueConstraint(
                fields=["origem", "destino_uf", "destino_cidade"], name="frete_unico_por_rota",
            ),
        ]

    def __str__(self):
        destino = f"{self.destino_cidade}/{self.destino_uf}" if self.destino_cidade else self.destino_uf
        return f"{self.origem} → {destino}: R$ {self.preco_kg}/kg"

    def save(self, *args, **kwargs):
        self.destino_uf = self.destino_uf.upper().strip()
        self.destino_cidade = self.destino_cidade.upper().strip()
        super().save(*args, **kwargs)

    @classmethod
    def buscar(cls, origem: Local, cidade: str, uf: str) -> "TabelaFrete | None":
        uf = (uf or "").upper().strip()
        cidade = (cidade or "").upper().strip()
        qs = cls.objects.filter(origem=origem, destino_uf=uf)
        if cidade:
            exato = qs.filter(destino_cidade=cidade).first()
            if exato:
                return exato
        return qs.filter(destino_cidade="").first()


class Pedido(models.Model):
    class Status(models.TextChoices):
        RASCUNHO = "RASC", "Rascunho"
        CONFIRMADO = "CONF", "Confirmado (reservado)"
        FATURADO = "FAT", "Faturado"
        CANCELADO = "CANC", "Cancelado"

    numero = models.PositiveIntegerField("número", unique=True, editable=False)
    data = models.DateField()
    cliente = models.ForeignKey(
        Parceiro, on_delete=models.PROTECT, related_name="pedidos",
        limit_choices_to={"eh_cliente": True},
    )
    contrato = models.ForeignKey(
        Contrato, on_delete=models.PROTECT, null=True, blank=True, related_name="pedidos",
        limit_choices_to={"tipo": Contrato.Tipo.VENDA},
    )
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    quantidade_kg = models.DecimalField("quantidade (kg)", max_digits=14, decimal_places=3)
    preco_unitario = models.DecimalField("preço bruto (R$/kg)", max_digits=12, decimal_places=4)
    destino_cidade = models.CharField("cidade de entrega", max_length=100)
    destino_uf = models.CharField("UF de entrega", max_length=2)
    exportacao = models.BooleanField(
        "exportação", default=False, help_text="Venda para fora do país: sem ICMS, PIS e COFINS.",
    )
    icms_venda = models.DecimalField(
        "ICMS na venda (%)", max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Em branco: 18% dentro de SP, 12% para outros estados.",
    )
    icms_frete = models.DecimalField(
        "ICMS do frete (%)", max_digits=5, decimal_places=2, default=tributos.ICMS_FRETE_PADRAO,
        help_text="ICMS destacado no CT-e do frete da venda, que vira crédito. 12% é o comum; 0 se a transportadora não destaca.",
    )
    status = models.CharField(max_length=4, choices=Status.choices, default=Status.RASCUNHO)
    nota = models.ForeignKey(
        NotaFiscal, on_delete=models.PROTECT, null=True, blank=True, related_name="pedidos",
        help_text="NF de venda emitida no ERP antigo.",
    )
    data_faturamento = models.DateField(null=True, blank=True)
    observacao = models.TextField("observação", blank=True)
    # Venda com mais de um produto: cada produto é um pedido (tem seus próprios
    # tanques e margem); os outros apontam para o primeiro.
    pedido_principal = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="adicionais",
        verbose_name="mesma venda que",
    )
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-numero"]

    def __str__(self):
        return f"Pedido {self.numero} - {self.cliente}"

    def save(self, *args, **kwargs):
        if not self.numero:
            ultimo = Pedido.objects.aggregate(m=models.Max("numero"))["m"] or 0
            self.numero = ultimo + 1
        self.destino_uf = (self.destino_uf or "").upper().strip()
        if self.exportacao:
            self.icms_venda = Decimal("0")
        elif self.icms_venda is None:
            self.icms_venda = impostos.icms_venda_padrao(self.destino_uf)
        super().save(*args, **kwargs)

    # Totais ---------------------------------------------------------------
    @property
    def receita(self) -> Decimal:
        return self.quantidade_kg * self.preco_unitario

    def _soma_origens(self, expr) -> Decimal:
        return self.origens.aggregate(
            t=Sum(expr, output_field=models.DecimalField(max_digits=20, decimal_places=4))
        )["t"] or ZERO

    @property
    def quantidade_alocada(self) -> Decimal:
        return self._soma_origens(F("quantidade_kg"))

    @property
    def custo_produto(self) -> Decimal:
        return self._soma_origens(F("quantidade_kg") * F("custo_unitario"))

    @property
    def custo_frete(self) -> Decimal:
        return self._soma_origens(F("quantidade_kg") * F("frete_kg"))

    @property
    def custo_total(self) -> Decimal:
        return self.custo_produto + self.custo_frete

    @property
    def margem(self) -> Decimal:
        """Receita bruta menos custo do produto e frete (antes de impostos)."""
        return self.receita - self.custo_total

    @property
    def margem_kg(self) -> Decimal:
        return self.margem / self.quantidade_kg if self.quantidade_kg else ZERO

    # Impostos desta venda (regras em apps/fiscal/tributos.py) -------------
    @property
    def debitos(self) -> Impostos:
        """ICMS, PIS e COFINS que nascem na venda."""
        return tributos.debito_venda(self.receita, self.icms_venda or 0, self.exportacao)

    @property
    def creditos_produto(self) -> Impostos:
        """Créditos que vieram na compra do produto, dos lotes/tanques usados."""
        total = Impostos()
        for o in self.origens.all():
            total = total + o.creditos_kg.vezes(o.quantidade_kg)
        return total

    @property
    def creditos_frete(self) -> Impostos:
        """O frete da venda é pago pela JT (CIF): o CT-e também dá crédito."""
        return tributos.credito_frete(self.custo_frete, self.icms_frete)

    @property
    def creditos(self) -> Impostos:
        return self.creditos_produto + self.creditos_frete

    @property
    def a_pagar(self) -> Impostos:
        """Débito − créditos, imposto por imposto. Negativo = sobra crédito
        (fica no saldo credor do mês e abate as próximas vendas)."""
        return self.debitos - self.creditos

    @property
    def fator_venda(self) -> Decimal:
        return impostos.fator(self.icms_venda or 0, pis_cofins=not self.exportacao)

    @property
    def fator_compra(self) -> Decimal:
        return impostos.fator(self.produto.icms_compra)

    @property
    def receita_net(self) -> Decimal:
        return self.receita - self.debitos.total

    @property
    def custo_produto_net(self) -> Decimal:
        return self.custo_produto - self.creditos_produto.total

    @property
    def custo_frete_net(self) -> Decimal:
        return self.custo_frete - self.creditos_frete.total

    @property
    def custo_total_net(self) -> Decimal:
        return self.custo_produto_net + self.custo_frete_net

    @property
    def margem_net(self) -> Decimal:
        """Margem real: o que sobra depois de pagar compra, frete e o imposto líquido."""
        return self.receita_net - self.custo_total_net

    @property
    def margem_net_kg(self) -> Decimal:
        return self.margem_net / self.quantidade_kg if self.quantidade_kg else ZERO

    @property
    def impostos_venda(self) -> Decimal:
        """ICMS + PIS + COFINS a pagar por esta venda, já descontados os créditos."""
        return self.a_pagar.total

    @property
    def mesma_venda(self) -> list["Pedido"]:
        """Os outros pedidos (produtos) lançados junto com este na mesma venda."""
        principal = self.pedido_principal or self
        grupo = [principal, *principal.adicionais.all()]
        return [p for p in grupo if p.pk != self.pk]

    @property
    def editavel(self) -> bool:
        return self.status == self.Status.RASCUNHO


class PedidoOrigem(CreditosPorKg):
    """Uma parte do pedido que sai de um tanque (local)."""

    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="origens")
    local = models.ForeignKey(Local, on_delete=models.PROTECT)
    lote = models.ForeignKey(Lote, on_delete=models.PROTECT, null=True, blank=True)
    quantidade_kg = models.DecimalField("quantidade (kg)", max_digits=14, decimal_places=3)
    custo_unitario = models.DecimalField(
        "custo previsto (R$/kg)", max_digits=12, decimal_places=4, default=0,
        help_text="Custo do produto no tanque quando a origem foi escolhida; no faturamento vira o custo real.",
    )
    frete_kg = models.DecimalField("frete (R$/kg)", max_digits=8, decimal_places=4, default=0)
    transportadora = models.ForeignKey(
        Parceiro, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        limit_choices_to={"eh_transportadora": True},
    )
    movimentacao = models.OneToOneField(
        Movimentacao, on_delete=models.PROTECT, null=True, blank=True, related_name="origem_pedido",
    )

    class Meta:
        ordering = ["pedido", "id"]
        verbose_name = "origem do pedido"
        verbose_name_plural = "origens do pedido"

    def __str__(self):
        return f"{self.quantidade_kg} kg de {self.local}"

    @property
    def custo_total_kg(self) -> Decimal:
        return self.custo_unitario + self.frete_kg
