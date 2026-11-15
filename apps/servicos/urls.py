from django.urls import path

from . import views

app_name = "servicos"

urlpatterns = [
    path("", views.lista, name="lista"),
    path("nova/", views.nova, name="nova"),
    path("<int:pk>/", views.detalhe, name="detalhe"),
    path("<int:pk>/editar/", views.editar, name="editar"),
    path("<int:pk>/concluir/", views.concluir, name="concluir"),
    path("<int:pk>/reabrir/", views.reabrir, name="reabrir"),
    path("<int:pk>/cancelar/", views.cancelar, name="cancelar"),
    path("<int:pk>/faturar/", views.faturar, name="faturar"),
    path("<int:pk>/corrigir/", views.corrigir, name="corrigir"),
    path("recipientes/", views.recipientes, name="recipientes"),
    path("recipientes/<int:pk>/usar-tmo/", views.usar_tmo, name="usar_tmo"),
    path("operador/", views.operador, name="operador"),
    path("operador/<int:pk>/", views.operador_os, name="operador_os"),
]
