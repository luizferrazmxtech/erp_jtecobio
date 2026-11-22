"""
Estoque: o saldo de cada local é sempre a soma das movimentações.

Ninguém digita saldo. Para corrigir uma diferença, lança-se um ajuste de
inventário com motivo, que fica registrado com o usuário e a data.
Toda criação de movimentação deve passar por `apps.estoque.services`.
"""

from django.conf import settings
from django.db import models

from apps.cadastros.models import Local, Produto
from apps.contratos.models import Contrato
from apps.fiscal.models import NotaFiscal
from apps.fiscal.tributos import Impostos


class CreditosPorKg(models.Model):
    """Impostos que a JT recupera, por kg: o crédito de ICMS, PIS e COFINS
    que veio na compra. Custo net (o que conta para a margem) = custo − créditos.

    Fica no lote, em cada movimentação (a saída leva o crédito médio do
    tanque, como o custo) e em cada origem de pedido.
    """

    credito_icms_kg = models.DecimalField("crédito de ICMS (R$/kg)", max_digits=12, decimal_places=6, default=0)
    credito_pis_kg = models.DecimalField("crédito de PIS (R$/kg)", max_digits=12, decimal_places=6, default=0)
    credito_cofins_kg = models.DecimalField("crédito de COFINS (R$/kg)", max_digits=12, decimal_places=6, default=0)

    class Meta:
        abstract = True

    @property
    def creditos_kg(self) -> Impostos:
        return Impostos(self.credito_icms_kg, self.credito_pis_kg, self.credito_cofins_kg)

    def definir_creditos(self, creditos: Impostos) -> None:
        self.credito_icms_kg = creditos.icms
        self.credito_pis_kg = creditos.pis
        self.credito_cofins_kg = creditos.cofins

    @property
    def credito_total_kg(self):
        return self.credito_icms_kg + self.credito_pis_kg + self.credito_cofins_kg


class Configuracao(models.Model):
    """Configurações gerais do sistema (existe uma única linha)."""

    class MetodoCusto(models.TextChoices):
        MEDIO = "MEDIO", "Custo médio por local"
        LOTE = "LOTE", "Custo por lote (rastreia a origem)"

    metodo_custo = models.CharField(
        "método de custo", max_length=5, choices=MetodoCusto.choices, default=MetodoCusto.MEDIO,
        help_text=(
            "Custo médio: a saída usa o custo médio do produto naquele local. "
            "Por lote: a saída precisa informar de qual lote sai e usa o custo dele."
        ),
    )
    permitir_saldo_negativo = models.BooleanField(
        default=False,
        help_text="Deixe desmarcado: o sistema recusa saídas maiores que o saldo.",
    )

    class Meta:
        verbose_name = "configuração"
        verbose_name_plural = "configuração"

    def __str__(self):
        return "Configuração do sistema"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def atual(cls) -> "Configuracao":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Lote(CreditosPorKg):
    """Um lote é uma entrada identificável de produto: uma NF de compra,
    um retorno de industrialização, um saldo inicial."""

    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name="lotes")
    codigo = models.CharField("código", max_length=60)
    data = models.DateField()
    custo_unitario = models.DecimalField("custo (R$/kg)", max_digits=12, decimal_places=4)
    nota_entrada = models.ForeignKey(
        NotaFiscal, on_delete=models.SET_NULL, null=True, blank=True, related_name="lotes",
    )
    origem = models.CharField(max_length=200, blank=True, help_text="Ex.: usina, contrato, industrialização")
    icms_compra = models.DecimalField(
        "ICMS da compra (%)", max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Alíquota de ICMS da nota de compra (12% de fora de SP, 18% dentro).",
    )

    class Meta:
        ordering = ["data", "codigo"]
        constraints = [
            models.UniqueConstraint(fields=["produto", "codigo"], name="lote_unico_por_produto"),
        ]

    def __str__(self):
        return f"{self.codigo} ({self.produto})"


class Movimentacao(CreditosPorKg):
    class Tipo(models.TextChoices):
        SALDO_INICIAL = "INICIAL", "Saldo inicial"
        ENTRADA = "ENTRADA", "Entrada (compra)"
        SAIDA = "SAIDA", "Saída (venda)"
        TRANSF_SAIDA = "TR_SAI", "Transferência - saída"
        TRANSF_ENTRADA = "TR_ENT", "Transferência - entrada"
        REMESSA_INDUSTR = "REM_IND", "Remessa para industrialização"
        RETORNO_INDUSTR = "RET_IND", "Retorno de industrialização"
        PERDA = "PERDA", "Perda"
        AJUSTE = "AJUSTE", "Ajuste de inventário"

    data = models.DateField()
    tipo = models.CharField(max_length=8, choices=Tipo.choices)
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name="movimentacoes")
    local = models.ForeignKey(Local, on_delete=models.PROTECT, related_name="movimentacoes")
    lote = models.ForeignKey(Lote, on_delete=models.PROTECT, null=True, blank=True, related_name="movimentacoes")
    quantidade_kg = models.DecimalField(
        "quantidade (kg)", max_digits=14, decimal_places=3,
        help_text="Positiva para entradas, negativa para saídas.",
    )
    custo_unitario = models.DecimalField("custo (R$/kg)", max_digits=12, decimal_places=4, default=0)

    nota = models.ForeignKey(NotaFiscal, on_delete=models.PROTECT, null=True, blank=True, related_name="movimentacoes")
    contrato = models.ForeignKey(Contrato, on_delete=models.SET_NULL, null=True, blank=True, related_name="movimentacoes")
    # Liga as duas pontas de uma transferência
    par = models.OneToOneField("self", on_delete=models.SET_NULL, null=True, blank=True)

    documento = models.CharField(max_length=100, blank=True, help_text="Ex.: NF 3852, CT-e 7467")
    observacao = models.TextField("observação / motivo", blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data", "-id"]
        verbose_name = "movimentação"
        verbose_name_plural = "movimentações"
        indexes = [models.Index(fields=["produto", "local", "data"])]

    def __str__(self):
        return f"{self.data:%d/%m/%Y} {self.get_tipo_display()} {self.quantidade_kg} kg {self.produto} @ {self.local}"

    @property
    def valor(self):
        return self.quantidade_kg * self.custo_unitario
