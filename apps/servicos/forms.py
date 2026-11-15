from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from apps.cadastros.models import Parceiro, Produto
from apps.web.busca import BuscaSelect

from .models import Apontamento, OrdemServico, TipoRecipiente


class OrdemServicoForm(forms.ModelForm):
    """Dados da OS. Os recipientes vêm em linhas à parte (ver views._linhas_recipientes)."""

    quantidade = forms.DecimalField(label="Quantidade que chegou", min_value=Decimal("0.001"),
                                    max_digits=14, decimal_places=3, localize=True)
    unidade_quantidade = forms.ChoiceField(label="Em", choices=[("KG", "kg"), ("L", "litros")], initial="KG")

    class Meta:
        model = OrdemServico
        fields = ["data", "cliente", "produto", "descricao_produto", "densidade", "placa", "nf_cliente",
                  "pedido_cliente", "lote", "aliquota_iss", "aliquota_pis", "aliquota_cofins", "observacao"]
        localized_fields = ["densidade", "aliquota_iss", "aliquota_pis", "aliquota_cofins"]
        widgets = {
            "data": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "observacao": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["data"].initial = timezone.localdate()
        self.fields["cliente"].queryset = Parceiro.objects.filter(ativo=True)
        self.fields["cliente"].widget = BuscaSelect(papel="cliente", placeholder="Digite o nome, CNPJ ou cidade do cliente…")
        self.fields["cliente"].widget.choices = self.fields["cliente"].choices
        # Busca digitável: os produtos que o cliente já envasou aparecem primeiro.
        self.fields["produto"].queryset = Produto.objects.filter(
            Q(ativo=True) | Q(pk=self.instance.produto_id or 0)).order_by("nome")
        self.fields["produto"].widget = BuscaSelect(
            url_nome="web:busca_produtos", depende="cliente",
            placeholder="Clique para ver os produtos deste cliente ou digite o nome…",
            vazio="Este cliente ainda não tem produto em OS anteriores. Digite o nome para buscar, ou deixe em branco e preencha a descrição.")
        self.fields["produto"].widget.choices = self.fields["produto"].choices
        self.fields["produto"].help_text = "Opcional. Se o produto do cliente não estiver cadastrado, deixe em branco e preencha a descrição."
        if self.instance.pk:
            self.fields["quantidade"].initial = self.instance.quantidade_kg

    def clean(self):
        dados = super().clean()
        if not dados.get("produto") and not dados.get("descricao_produto"):
            self.add_error("descricao_produto", "Escolha um produto ou descreva o produto do cliente.")
        qtd, dens = dados.get("quantidade"), dados.get("densidade")
        if qtd is not None:
            if dados.get("unidade_quantidade") == "L":
                if not dens:
                    self.add_error("densidade", "Informe a densidade para converter litros em kg.")
                    return dados
                qtd = qtd * dens
            dados["quantidade_kg"] = qtd.quantize(Decimal("0.001"))
        return dados

    def save(self, commit=True):
        self.instance.quantidade_kg = self.cleaned_data["quantidade_kg"]
        return super().save(commit)


class TipoRecipienteForm(forms.ModelForm):
    class Meta:
        model = TipoRecipiente
        fields = ["nome", "capacidade", "unidade", "preco_kg", "tempo_padrao_min", "ativo"]
        localized_fields = ["capacidade", "preco_kg", "tempo_padrao_min"]


class CorrecaoForm(forms.Form):
    """Lançamento manual de um supervisor (o funcionário esqueceu de apontar)."""

    funcionario = forms.ModelChoiceField(label="Funcionário", queryset=None)
    evento = forms.ChoiceField(label="O que aconteceu", choices=Apontamento.Evento.choices)
    momento = forms.DateTimeField(label="Data e hora", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
                                  input_formats=["%Y-%m-%dT%H:%M"])
    motivo = forms.ChoiceField(label="Motivo (se for pausa)", choices=[("", "—")] + Apontamento.Motivo.choices,
                               required=False)
    observacao = forms.CharField(label="Por que está corrigindo", max_length=200)

    def __init__(self, *a, **k):
        from django.contrib.auth import get_user_model

        super().__init__(*a, **k)
        self.fields["funcionario"].queryset = get_user_model().objects.filter(is_active=True).order_by("first_name", "username")
        self.fields["funcionario"].label_from_instance = lambda u: u.get_full_name() or u.username
