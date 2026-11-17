from django import forms
from django.utils import timezone

from apps.cadastros.models import Parceiro, Produto
from apps.contratos.models import Contrato
from apps.fiscal.models import NotaFiscal

from apps.web.busca import BuscaSelect

from .models import Pedido


class PedidoForm(forms.ModelForm):
    # Outro produto na mesma venda (ex.: atender o contrato e levar mais algo junto).
    outro_produto = forms.BooleanField(label="Incluir outro produto nesta venda", required=False)
    produto2 = forms.ModelChoiceField(label="Outro produto", queryset=Produto.objects.none(), required=False)
    quantidade2 = forms.DecimalField(label="Quantidade do outro produto (kg)", required=False, min_value=0,
                                     max_digits=14, decimal_places=3)
    preco2 = forms.DecimalField(label="Preço do outro produto (R$/kg)", required=False, min_value=0,
                                max_digits=12, decimal_places=4)

    class Meta:
        model = Pedido
        fields = ["data", "cliente", "contrato", "produto", "quantidade_kg", "preco_unitario",
                  "destino_cidade", "destino_uf", "exportacao", "icms_venda", "icms_frete", "observacao"]
        widgets = {
            "data": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "observacao": forms.Textarea(attrs={"rows": 2}),
        }
        help_texts = {"destino_cidade": "Em branco: usa a cidade do cadastro do cliente."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["data"].initial = timezone.localdate()
        self.fields["cliente"].queryset = Parceiro.objects.filter(eh_cliente=True, ativo=True)
        self.fields["cliente"].widget = BuscaSelect(papel="cliente", placeholder="Digite o nome, CNPJ ou cidade do cliente…")
        self.fields["cliente"].widget.choices = self.fields["cliente"].choices
        self.fields["produto"].queryset = Produto.objects.filter(ativo=True, controla_estoque=True)
        self.fields["produto2"].queryset = self.fields["produto"].queryset
        # Só contratos de venda abertos do cliente escolhido (a tela filtra de novo ao trocar o cliente).
        contratos = Contrato.objects.filter(
            tipo=Contrato.Tipo.VENDA, status=Contrato.Status.ABERTO).select_related("parceiro", "produto")
        cliente = self.data.get("cliente") or self.initial.get("cliente") or self.instance.cliente_id
        if str(cliente or "").isdigit():
            contratos = contratos.filter(parceiro_id=cliente)
        else:
            contratos = contratos.none()
        self.fields["contrato"].queryset = contratos.order_by("-mes_referencia", "numero")
        self.fields["contrato"].label_from_instance = lambda c: f"{c.numero} · {c.produto} · R$ {str(c.preco_unitario).replace('.', ',')}/kg"
        self.fields["contrato"].empty_label = "Sem contrato"
        self.fields["contrato"].help_text = "Só aparecem os contratos de venda abertos deste cliente."
        self.fields["destino_cidade"].required = False
        self.fields["icms_frete"].required = False
        self.fields["destino_uf"].required = False

    def clean(self):
        dados = super().clean()
        if dados.get("icms_frete") is None:
            from apps.fiscal.tributos import ICMS_FRETE_PADRAO
            dados["icms_frete"] = ICMS_FRETE_PADRAO
        cliente = dados.get("cliente")
        if cliente:
            if not dados.get("destino_cidade"):
                dados["destino_cidade"] = cliente.cidade
            if not dados.get("destino_uf"):
                dados["destino_uf"] = cliente.uf
        if not dados.get("destino_uf"):
            self.add_error("destino_uf", "Informe a UF de entrega (o cliente não tem UF no cadastro).")
        contrato = dados.get("contrato")
        if contrato and cliente and contrato.parceiro_id != cliente.pk:
            self.add_error("contrato", "Este contrato é de outro cliente.")
        if contrato and dados.get("produto") and contrato.produto_id != dados["produto"].pk:
            self.add_error("contrato", "Este contrato é de outro produto.")
        if dados.get("quantidade_kg") is not None and dados["quantidade_kg"] <= 0:
            self.add_error("quantidade_kg", "A quantidade deve ser maior que zero.")
        if dados.get("outro_produto"):
            for campo, msg in (("produto2", "Escolha o outro produto."),
                               ("quantidade2", "Informe a quantidade do outro produto."),
                               ("preco2", "Informe o preço do outro produto.")):
                if not dados.get(campo):
                    self.add_error(campo, msg)
            if dados.get("produto2") and dados.get("produto2") == dados.get("produto"):
                self.add_error("produto2", "Escolha um produto diferente do primeiro; para mais quantidade, aumente a do primeiro.")
        return dados


class FaturarForm(forms.Form):
    data = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}))
    nota = forms.ModelChoiceField(
        queryset=NotaFiscal.objects.none(), required=False, label="NF de venda",
        help_text="As NFs vêm do sistema antigo (importação diária); se ainda não apareceu, fature e ligue depois.",
    )

    def __init__(self, *args, pedido=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = NotaFiscal.objects.filter(direcao=NotaFiscal.Direcao.SAIDA, cancelada=False)
        if pedido is not None:
            qs = qs.filter(parceiro=pedido.cliente)
        self.fields["nota"].queryset = qs
