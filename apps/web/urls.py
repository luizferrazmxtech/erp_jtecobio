from django.urls import path

from . import busca, sistema, views

app_name = "web"

urlpatterns = [
    path("", views.painel, name="painel"),
    path("estoque/movimentacoes/", views.movimentacoes, name="movimentacoes"),
    path("vendas/fretes/", views.fretes, name="fretes"),
    path("contratos/", views.contratos, name="contratos"),
    path("contratos/<int:pk>/", views.contrato, name="contrato"),
    path("contratos/<int:pk>/notas/", views.contrato_ligar_nota, name="contrato_ligar_nota"),
    path("fiscal/", views.notas, name="notas"),
    path("fiscal/<int:pk>/", views.nota, name="nota"),
    path("fiscal/impostos/", views.impostos, name="impostos"),
    path("cadastros/parceiros/", views.parceiros, name="parceiros"),
    path("cadastros/parceiros/<int:pk>/", views.parceiro, name="parceiro"),
    path("cadastros/produtos/", views.produtos, name="produtos"),
    path("cadastros/locais/", views.locais, name="locais"),
    path("novo/<str:nome>/", views.adicionar, name="adicionar"),
    path("editar/<str:nome>/<int:pk>/", views.editar, name="editar"),
    path("busca/parceiros/", busca.parceiros, name="busca_parceiros"),
    path("busca/produtos/", busca.produtos, name="busca_produtos"),
    path("sistema/usuarios/", sistema.usuarios, name="usuarios"),
    path("sistema/usuarios/novo/", sistema.usuario, name="usuario_novo"),
    path("sistema/usuarios/<int:pk>/", sistema.usuario, name="usuario"),
    path("sistema/configuracoes/", sistema.configuracoes, name="configuracoes"),
    path("sistema/conferir-planilha/", views.conferir_planilha, name="conferir_planilha"),
]
