from django.contrib import admin

from .models import Configuracao, Lote, Movimentacao


@admin.register(Configuracao)
class ConfiguracaoAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not Configuracao.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Lote)
class LoteAdmin(admin.ModelAdmin):
    list_display = ["codigo", "produto", "data", "custo_unitario", "nota_entrada", "origem"]
    list_filter = ["produto"]
    search_fields = ["codigo", "origem"]
    readonly_fields = ["nota_entrada"]


@admin.register(Movimentacao)
class MovimentacaoAdmin(admin.ModelAdmin):
    """Somente consulta: movimentações são criadas pelas telas de estoque,
    que validam saldo e custo. Para corrigir, lance um ajuste."""

    list_display = ["data", "tipo", "produto", "local", "lote", "quantidade_kg", "custo_unitario", "documento", "usuario"]
    list_filter = ["tipo", "produto", "local", "data"]
    search_fields = ["documento", "observacao", "lote__codigo"]
    date_hierarchy = "data"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
