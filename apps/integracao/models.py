from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class RegistroExemplo(models.Model):
    """Marca o que foi criado pelo comando `carregar_exemplos` (fretes e
    pedidos fictícios), para que `carregar_exemplos --apagar` remova só isso."""

    tipo = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    objeto_id = models.PositiveBigIntegerField()
    objeto = GenericForeignKey("tipo", "objeto_id")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "registro de exemplo"
        verbose_name_plural = "registros de exemplo"
        constraints = [models.UniqueConstraint(fields=["tipo", "objeto_id"], name="exemplo_unico")]

    @classmethod
    def marcar(cls, obj):
        cls.objects.get_or_create(tipo=ContentType.objects.get_for_model(obj), objeto_id=obj.pk)

    @classmethod
    def ids(cls, modelo) -> list[int]:
        return list(cls.objects.filter(tipo=ContentType.objects.get_for_model(modelo)).values_list("objeto_id", flat=True))

    @classmethod
    def existe(cls) -> bool:
        return cls.objects.exists()
