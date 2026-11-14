from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

admin.site.site_header = "ERP JT Ecobio"
admin.site.site_title = "ERP JT Ecobio"
admin.site.index_title = "Cadastros e consultas"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="estoque:saldos", permanent=False)),
    path("admin/", admin.site.urls),
    path("estoque/", include("apps.estoque.urls")),
    path("vendas/", include("apps.vendas.urls")),
]
