"""
Contratos de compra (com usinas) e de venda (com clientes).

O saldo nunca é digitado: é a quantidade contratada menos o que já tinha
sido entregue antes da implantação (`entregue_antes`) menos a soma dos
itens de nota fiscal vinculados ao contrato (notas canceladas não contam).
"""

from decimal import Decimal

from django.db import models
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce

from apps.cadastros.models import Parceiro, Produto


class ContratoQuerySet(models.QuerySet):
    def com_saldo(self):
        """Anota `quantidade_entregue` e `saldo` em cada contrato."""
        entregue = Coalesce(
            Sum("itens_nota__quantidade", filter=Q(itens_nota__nota__cancelada=False)),
            Decimal("0"),
            output_field=models.DecimalField(max_digits=14, decimal_places=4),
        )
        return self.annotate(quantidade_entregue=entregue + models.F("entregue_antes")).annotate(
            saldo=models.F("quantidade_kg") - models.F("quantidade_entregue")
        )


class Contrato(models.Model):
    class Tipo(models.TextChoices):
        COMPRA = "C", "Compra"
        VENDA = "V", "Venda"

    class Modalidade(models.TextChoices):
        MERCADO_INTERNO = "MI", "Mercado interno"
        EXPORTACAO = "EXP", "Exportação"
        INDUSTRIALIZACAO = "IND", "Industrialização"

    class Status(models.TextChoices):
        ABERTO = "ABERTO", "Aberto"
        FINALIZADO = "FINAL", "Finalizado"
        CANCELADO = "CANCEL", "Cancelado"

    numero = models.CharField("número", max_length=40)
    tipo = models.CharField(max_length=1, choices=Tipo.choices)
    parceiro = models.ForeignKey(Parceiro, on_delete=models.PROTECT, related_name="contratos")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    modalidade = models.CharField(max_length=3, choices=Modalidade.choices, default=Modalidade.MERCADO_INTERNO)
    quantidade_kg = models.DecimalField("quantidade (kg)", max_digits=14, decimal_places=3)
    preco_unitario = models.DecimalField("preço bruto (R$/kg)", max_digits=12, decimal_places=4)
    frete_previsto = models.DecimalField(
        "frete previsto (R$/kg)", max_digits=10, decimal_places=4, default=0,
    )
    entregue_antes = models.DecimalField(
        "entregue antes da implantação (kg)", max_digits=14, decimal_places=3, default=0,
        help_text="Quanto já tinha sido entregue quando o contrato foi carregado no sistema (ex.: pela planilha).",
    )
    mes_referencia = models.DateField("mês de entrega", help_text="Use o dia 1 do mês.")
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.ABERTO)
    observacao = models.TextField("observação", blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    objects = ContratoQuerySet.as_manager()

    class Meta:
        ordering = ["-mes_referencia", "numero"]
        constraints = [
            models.UniqueConstraint(fields=["tipo", "numero", "parceiro"], name="contrato_unico"),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.numero} - {self.parceiro}"

    @property
    def valor_total(self) -> Decimal:
        return self.quantidade_kg * self.preco_unitario

    def calcular_saldo(self) -> Decimal:
        entregue = self.itens_nota.filter(nota__cancelada=False).aggregate(
            total=Sum("quantidade")
        )["total"] or Decimal("0")
        return self.quantidade_kg - self.entregue_antes - entregue

    @property
    def percentual_entregue(self) -> int:
        if not self.quantidade_kg:
            return 0
        return max(0, min(100, round(100 * (self.quantidade_kg - self.calcular_saldo()) / self.quantidade_kg)))
