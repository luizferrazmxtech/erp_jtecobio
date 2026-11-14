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
from apps.estoque.models import Lote, Movimentacao
from apps.fiscal.models import NotaFiscal

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
    status = models.CharField(max_length=4, choices=Status.choices, default=Status.RASCUNHO)
    nota = models.ForeignKey(
        NotaFiscal, on_delete=models.PROTECT, null=True, blank=True, related_name="pedidos",
        help_text="NF de venda emitida no ERP antigo.",
    )
    data_faturamento = models.DateField(null=True, blank=True)
    observacao = models.TextField("observação", blank=True)
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

    @property
    def editavel(self) -> bool:
        return self.status == self.Status.RASCUNHO


class PedidoOrigem(models.Model):
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
