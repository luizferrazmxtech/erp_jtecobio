from django.contrib import admin
from django.contrib.humanize.templatetags.humanize import intcomma

from apps.fiscal.models import NotaItem

from .models import Contrato


class ItensVinculadosInline(admin.TabularInline):
    model = NotaItem
    fk_name = "contrato"
    extra = 0
    can_delete = False
    fields = ["nota", "descricao", "quantidade", "valor_total"]
    readonly_fields = fields
    verbose_name_plural = "notas que abatem este contrato"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Contrato)
class ContratoAdmin(admin.ModelAdmin):
    list_display = ["numero", "tipo", "parceiro", "produto", "modalidade", "mes_referencia",
                    "qtd", "entregue", "saldo_kg", "preco_unitario", "status"]
    list_filter = ["tipo", "modalidade", "status", "produto", "mes_referencia"]
    search_fields = ["numero", "parceiro__razao_social"]
    autocomplete_fields = ["parceiro", "produto"]
    date_hierarchy = "mes_referencia"
    inlines = [ItensVinculadosInline]

    def get_queryset(self, request):
        return super().get_queryset(request).com_saldo()

    @admin.display(description="contratado (kg)", ordering="quantidade_kg")
    def qtd(self, obj):
        return intcomma(round(obj.quantidade_kg))

    @admin.display(description="entregue (kg)", ordering="quantidade_entregue")
    def entregue(self, obj):
        return intcomma(round(obj.quantidade_entregue))

    @admin.display(description="saldo (kg)", ordering="saldo")
    def saldo_kg(self, obj):
        return intcomma(round(obj.saldo))
