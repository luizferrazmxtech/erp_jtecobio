from django.urls import path

from . import views

app_name = "estoque"

urlpatterns = [
    path("", views.saldos, name="saldos"),
    path("entrada/", views.entrada, name="entrada"),
    path("saida/", views.saida, name="saida"),
    path("transferencia/", views.transferencia, name="transferencia"),
    path("ajuste/", views.ajuste, name="ajuste"),
]
