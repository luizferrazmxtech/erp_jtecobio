from django.contrib import admin

from .models import Pedido, PedidoOrigem, TabelaFrete


@admin.register(TabelaFrete)
class TabelaFreteAdmin(admin.ModelAdmin):
    list_display = ["origem", "destino_uf", "destino_cidade", "preco_kg", "transportadora", "atualizado_em"]
    list_filter = ["origem", "destino_uf"]
    list_editable = ["preco_kg"]
    search_fields = ["destino_cidade", "origem__nome"]
    autocomplete_fields = ["transportadora"]


class OrigemInline(admin.TabularInline):
    model = PedidoOrigem
    extra = 0
    fields = ["local", "lote", "quantidade_kg", "custo_unitario", "frete_kg", "transportadora", "movimentacao"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    """Consulta. Pedidos são criados e alterados pela tela de vendas,
    que valida saldo e reserva."""

    list_display = ["numero", "data", "cliente", "produto", "quantidade_kg", "preco_unitario", "status", "nota"]
    list_filter = ["status", "produto", "data"]
    search_fields = ["numero", "cliente__razao_social"]
    date_hierarchy = "data"
    inlines = [OrigemInline]
    readonly_fields = [f.name for f in Pedido._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
