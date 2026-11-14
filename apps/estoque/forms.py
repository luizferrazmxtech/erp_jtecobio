from django import forms
from django.utils import timezone

from apps.cadastros.models import Local, Produto

from .models import Lote, Movimentacao


def _produtos():
    return Produto.objects.filter(ativo=True, controla_estoque=True)


def _locais():
    return Local.objects.filter(ativo=True)


class _Base(forms.Form):
    data = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    produto = forms.ModelChoiceField(queryset=Produto.objects.none())
    quantidade_kg = forms.DecimalField(label="Quantidade (kg)", min_value=0, decimal_places=3, max_digits=14)
    documento = forms.CharField(required=False, help_text="Ex.: NF 3852")
    observacao = forms.CharField(label="Observação", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["produto"].queryset = _produtos()


class EntradaForm(_Base):
    tipo = forms.ChoiceField(choices=[
        (Movimentacao.Tipo.ENTRADA, "Entrada (compra)"),
        (Movimentacao.Tipo.SALDO_INICIAL, "Saldo inicial (implantação)"),
        (Movimentacao.Tipo.RETORNO_INDUSTR, "Retorno de industrialização"),
    ])
    local = forms.ModelChoiceField(queryset=Local.objects.none())
    custo_unitario = forms.DecimalField(label="Custo (R$/kg)", min_value=0, decimal_places=4, max_digits=12)
    lote_codigo = forms.CharField(label="Código do lote", required=False,
                                  help_text="Deixe em branco para gerar automaticamente.")
    field_order = ["tipo", "data", "produto", "local", "quantidade_kg", "custo_unitario", "lote_codigo", "documento", "observacao"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["local"].queryset = _locais()


class SaidaForm(_Base):
    local = forms.ModelChoiceField(queryset=Local.objects.none())
    lote = forms.ModelChoiceField(queryset=Lote.objects.all(), required=False,
                                  help_text="Obrigatório quando o custo é por lote.")
    field_order = ["data", "produto", "local", "lote", "quantidade_kg", "documento", "observacao"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["local"].queryset = _locais()


class TransferenciaForm(_Base):
    origem = forms.ModelChoiceField(queryset=Local.objects.none())
    destino = forms.ModelChoiceField(queryset=Local.objects.none())
    lote = forms.ModelChoiceField(queryset=Lote.objects.all(), required=False)
    field_order = ["data", "produto", "origem", "destino", "lote", "quantidade_kg", "documento", "observacao"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["origem"].queryset = _locais()
        self.fields["destino"].queryset = _locais()


class AjusteForm(forms.Form):
    data = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    produto = forms.ModelChoiceField(queryset=Produto.objects.none())
    local = forms.ModelChoiceField(queryset=Local.objects.none())
    lote = forms.ModelChoiceField(queryset=Lote.objects.all(), required=False)
    quantidade_contada_kg = forms.DecimalField(label="Quantidade contada (kg)", min_value=0, decimal_places=3, max_digits=14)
    motivo = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["produto"].queryset = _produtos()
        self.fields["local"].queryset = _locais()
