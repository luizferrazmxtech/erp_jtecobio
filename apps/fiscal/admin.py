from django.contrib import admin

from .models import NotaFiscal, NotaItem


class NotaItemInline(admin.TabularInline):
    model = NotaItem
    extra = 0
    fields = ["item", "produto", "descricao", "unidade", "quantidade", "valor_total", "cfop", "contrato"]
    autocomplete_fields = ["produto", "contrato"]


@admin.register(NotaFiscal)
class NotaFiscalAdmin(admin.ModelAdmin):
    list_display = ["numero", "serie", "modelo", "direcao", "data_emissao", "parceiro", "cfop", "valor_total", "cancelada"]
    list_filter = ["direcao", "modelo", "cancelada", "data_emissao"]
    search_fields = ["numero", "chave", "parceiro__razao_social"]
    date_hierarchy = "data_emissao"
    autocomplete_fields = ["parceiro"]
    inlines = [NotaItemInline]
