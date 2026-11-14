from django.urls import path

from . import views

app_name = "vendas"

urlpatterns = [
    path("", views.lista, name="lista"),
    path("novo/", views.novo, name="novo"),
    path("<int:pk>/", views.detalhe, name="detalhe"),
    path("<int:pk>/origens/", views.salvar_origens, name="salvar_origens"),
    path("<int:pk>/sugerir/", views.sugerir, name="sugerir"),
    path("<int:pk>/confirmar/", views.confirmar, name="confirmar"),
    path("<int:pk>/reabrir/", views.reabrir, name="reabrir"),
    path("<int:pk>/cancelar/", views.cancelar, name="cancelar"),
    path("<int:pk>/faturar/", views.faturar, name="faturar"),
]
