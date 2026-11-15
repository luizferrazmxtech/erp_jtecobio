from django.contrib import admin
from django.urls import include, path

from apps.web.views import Entrar, Sair

admin.site.site_header = "ERP JT Ecobio"
admin.site.site_title = "ERP JT Ecobio"
admin.site.index_title = "Administração"

urlpatterns = [
    path("entrar/", Entrar.as_view(), name="entrar"),
    path("sair/", Sair.as_view(), name="sair"),
    path("admin/", admin.site.urls),
    path("", include("apps.web.urls")),
    path("estoque/", include("apps.estoque.urls")),
    path("vendas/", include("apps.vendas.urls")),
    path("servicos/", include("apps.servicos.urls")),
]
