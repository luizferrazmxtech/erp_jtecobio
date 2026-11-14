from django.contrib import admin

from .models import Local, Parceiro, Produto, Tanque


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ["codigo", "nome", "tipo", "unidade", "densidade", "controla_estoque", "ativo"]
    list_filter = ["tipo", "controla_estoque", "ativo"]
    search_fields = ["codigo", "nome"]


@admin.register(Parceiro)
class ParceiroAdmin(admin.ModelAdmin):
    list_display = ["razao_social", "documento", "cidade", "uf", "papeis", "ativo"]
    list_filter = ["eh_cliente", "eh_fornecedor", "eh_transportadora", "eh_industrializador", "eh_armazem", "uf"]
    search_fields = ["razao_social", "nome_fantasia", "documento"]
    fieldsets = [
        (None, {"fields": ["razao_social", "nome_fantasia", "documento", "inscricao_estadual", "cidade", "uf", "ativo"]}),
        ("Papéis", {"fields": ["eh_cliente", "eh_fornecedor", "eh_transportadora", "eh_industrializador", "eh_armazem"]}),
        ("ERP antigo", {"classes": ["collapse"], "fields": ["codigo_legado_cliente"]}),
    ]


class TanqueInline(admin.TabularInline):
    model = Tanque
    extra = 0
    autocomplete_fields = ["produto"]


@admin.register(Local)
class LocalAdmin(admin.ModelAdmin):
    list_display = ["nome", "tipo", "parceiro", "cidade", "uf", "ativo"]
    list_filter = ["tipo", "ativo"]
    search_fields = ["nome", "cidade"]
    autocomplete_fields = ["parceiro"]
    inlines = [TanqueInline]
