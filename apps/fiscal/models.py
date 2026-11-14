"""
Notas fiscais (NF-e e CT-e). O novo sistema não emite notas: elas
continuam sendo emitidas no ERP antigo e são registradas aqui para
abater contratos e gerar movimentações de estoque.
"""

from django.db import models

from apps.cadastros.models import Parceiro, Produto


class NotaFiscal(models.Model):
    class Direcao(models.TextChoices):
        ENTRADA = "E", "Entrada"
        SAIDA = "S", "Saída"

    class Modelo(models.TextChoices):
        NFE = "55", "NF-e"
        CTE = "57", "CT-e"
        NF_SERVICO = "01", "Nota de serviço / outros"
        OUTRO = "99", "Outro"

    direcao = models.CharField("entrada/saída", max_length=1, choices=Direcao.choices)
    modelo = models.CharField(max_length=2, choices=Modelo.choices, default=Modelo.NFE)
    numero = models.BigIntegerField("número")
    serie = models.CharField("série", max_length=5, blank=True)
    parceiro = models.ForeignKey(Parceiro, on_delete=models.PROTECT, related_name="notas")
    data_emissao = models.DateField("emissão")
    data_entrada = models.DateField("entrada/saída", null=True, blank=True)
    cfop = models.CharField("CFOP", max_length=4, blank=True)
    natureza = models.CharField("natureza da operação", max_length=120, blank=True)
    chave = models.CharField("chave de acesso", max_length=44, blank=True, db_index=True)

    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_icms = models.DecimalField("ICMS", max_digits=14, decimal_places=2, default=0)
    valor_ipi = models.DecimalField("IPI", max_digits=14, decimal_places=2, default=0)
    valor_pis = models.DecimalField("PIS", max_digits=14, decimal_places=2, default=0)
    valor_cofins = models.DecimalField("COFINS", max_digits=14, decimal_places=2, default=0)
    valor_frete = models.DecimalField("frete", max_digits=14, decimal_places=2, default=0)

    cancelada = models.BooleanField(default=False)
    motivo_cancelamento = models.CharField(max_length=200, blank=True)
    observacao = models.TextField("observação", blank=True)

    class Meta:
        ordering = ["-data_emissao", "-numero"]
        verbose_name = "nota fiscal"
        verbose_name_plural = "notas fiscais"
        indexes = [models.Index(fields=["direcao", "numero"])]

    def __str__(self):
        tipo = "CT-e" if self.modelo == self.Modelo.CTE else "NF"
        return f"{tipo} {self.numero} ({self.get_direcao_display().lower()}) - {self.parceiro}"


class NotaItem(models.Model):
    nota = models.ForeignKey(NotaFiscal, on_delete=models.CASCADE, related_name="itens")
    item = models.PositiveIntegerField()
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, null=True, blank=True)
    descricao = models.CharField("descrição", max_length=200)
    unidade = models.CharField(max_length=6, blank=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=4)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_icms = models.DecimalField("ICMS", max_digits=14, decimal_places=2, default=0)
    valor_ipi = models.DecimalField("IPI", max_digits=14, decimal_places=2, default=0)
    valor_pis = models.DecimalField("PIS", max_digits=14, decimal_places=2, default=0)
    valor_cofins = models.DecimalField("COFINS", max_digits=14, decimal_places=2, default=0)
    cfop = models.CharField("CFOP", max_length=4, blank=True)

    contrato = models.ForeignKey(
        "contratos.Contrato", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="itens_nota",
        help_text="Contrato que esta nota abate. O saldo do contrato é calculado a partir daqui.",
    )

    class Meta:
        ordering = ["nota", "item"]
        constraints = [
            models.UniqueConstraint(fields=["nota", "item"], name="item_unico_por_nota"),
        ]
        verbose_name = "item de nota"
        verbose_name_plural = "itens de nota"

    def __str__(self):
        return f"{self.nota.numero}/{self.item} - {self.descricao}"

    @property
    def preco_unitario(self):
        return self.valor_total / self.quantidade if self.quantidade else 0
