"""
Cadastros básicos: produtos, parceiros (clientes, fornecedores, etc.),
locais de estoque e tanques.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class Produto(models.Model):
    class Tipo(models.TextChoices):
        GLICERINA_80 = "GLIC80", "Glicerina 80% (bruta)"
        GBD_GRANEL = "GBD_GRANEL", "Glicerina bi-destilada (granel)"
        GBD_FRACIONADA = "GBD_FRAC", "Glicerina bi-destilada (fracionada)"
        ETANOL = "ETANOL", "Etanol / álcool"
        LECITINA = "LECITINA", "Lecitina"
        EMBALAGEM = "EMBALAGEM", "Embalagem"
        SERVICO = "SERVICO", "Serviço"
        OUTRO = "OUTRO", "Outro"

    codigo = models.CharField("código", max_length=30, unique=True)
    nome = models.CharField(max_length=200)
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.OUTRO)
    unidade = models.CharField(
        max_length=6, default="KG",
        help_text="Unidade em que o produto é controlado no estoque (normalmente KG).",
    )
    densidade = models.DecimalField(
        "densidade (kg/L)", max_digits=6, decimal_places=4, null=True, blank=True,
        help_text="Usada para converter litros em kg (ex.: glicerina ≈ 1,26).",
    )
    ncm = models.CharField("NCM", max_length=10, blank=True)
    controla_estoque = models.BooleanField(default=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def litros_para_kg(self, litros: Decimal) -> Decimal:
        if not self.densidade:
            raise ValidationError(f"O produto {self} não tem densidade cadastrada.")
        return Decimal(litros) * self.densidade


class Parceiro(models.Model):
    """Qualquer empresa com quem a JT se relaciona. Um mesmo CNPJ pode ter
    vários papéis (a Aroma, por exemplo, é cliente e industrializadora)."""

    documento = models.CharField(
        "CNPJ/CPF", max_length=14, blank=True, db_index=True,
        help_text="Somente números.",
    )
    razao_social = models.CharField("razão social", max_length=200)
    nome_fantasia = models.CharField(max_length=200, blank=True)
    inscricao_estadual = models.CharField("inscrição estadual", max_length=20, blank=True)
    cidade = models.CharField(max_length=100, blank=True)
    uf = models.CharField("UF", max_length=2, blank=True)

    eh_cliente = models.BooleanField("cliente", default=False)
    eh_fornecedor = models.BooleanField("fornecedor", default=False)
    eh_transportadora = models.BooleanField("transportadora", default=False)
    eh_industrializador = models.BooleanField("industrializador", default=False)
    eh_armazem = models.BooleanField("armazém / distribuidora", default=False)

    # Código do cliente no ERP antigo (só os clientes são importados de lá).
    codigo_legado_cliente = models.IntegerField(
        "código do cliente no ERP antigo", null=True, blank=True, db_index=True,
    )

    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["razao_social"]

    def __str__(self):
        return self.nome_fantasia or self.razao_social

    def papeis(self) -> str:
        nomes = [
            ("cliente", self.eh_cliente),
            ("fornecedor", self.eh_fornecedor),
            ("transportadora", self.eh_transportadora),
            ("industrializador", self.eh_industrializador),
            ("armazém", self.eh_armazem),
        ]
        return ", ".join(n for n, ativo in nomes if ativo)


class Local(models.Model):
    """Onde o produto está fisicamente (ou contabilmente).

    Cada distribuidora tem um único tanque por produto, então o saldo é
    controlado por local + produto (+ lote, quando o custo for por lote).
    """

    class Tipo(models.TextChoices):
        TANQUE_TERCEIRO = "TANQUE", "Tanque em distribuidora / terceiro"
        INDUSTRIALIZADOR = "INDUSTR", "Industrializador (Aroma, Biobrotas…)"
        PROPRIO = "PROPRIO", "Estoque próprio JT"
        ARMAZEM = "ARMAZEM", "Armazém de fracionados"
        TRANSITO = "TRANSITO", "Em trânsito"
        DIRETO = "DIRETO", "Virtual: venda direta usina → cliente"
        TERCEIROS = "TERCEIROS", "Produto de cliente (serviço de envase)"

    nome = models.CharField(max_length=100, unique=True)
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    parceiro = models.ForeignKey(
        Parceiro, on_delete=models.PROTECT, null=True, blank=True,
        help_text="Dono do local (distribuidora, industrializador, armazém).",
    )
    cidade = models.CharField(max_length=100, blank=True)
    uf = models.CharField("UF", max_length=2, blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]
        verbose_name_plural = "locais"

    def __str__(self):
        return self.nome

    @property
    def eh_de_terceiros(self) -> bool:
        """Produto de cliente: não entra no custo nem na DRE da JT."""
        return self.tipo == self.Tipo.TERCEIROS


class Tanque(models.Model):
    """Capacidade de um local para um produto. Um tanque por produto por local."""

    local = models.ForeignKey(Local, on_delete=models.CASCADE, related_name="tanques")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    capacidade_kg = models.DecimalField("capacidade (kg)", max_digits=14, decimal_places=3)
    identificacao = models.CharField(max_length=50, blank=True, help_text="Ex.: TQ-03")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["local", "produto"], name="um_tanque_por_produto_por_local"),
        ]

    def __str__(self):
        return f"{self.local} / {self.produto}"
