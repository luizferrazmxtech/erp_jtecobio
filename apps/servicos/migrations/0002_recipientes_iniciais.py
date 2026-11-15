"""Tipos de recipiente iniciais, com os preços da planilha DRE 10/2026
(aba PS Peter: flexitank 0,12; IBC 0,25; bombonas/tambores 0,30; octabins 0,40).

Os tempos padrão são uma ESTIMATIVA inicial para a OS já mostrar uma previsão.
Ajuste em Serviços → Tipos de recipiente; depois de algumas OS o sistema
mostra o TMO real de cada tipo e sugere trocar."""

from decimal import Decimal

from django.db import migrations

INICIAIS = [
    # nome, capacidade, unidade, R$/kg, minutos por recipiente
    ("Flexitank 24.000 L", "24000", "L", "0.12", "90"),
    ("IBC 1.000 L", "1000", "L", "0.25", "12"),
    ("Octabin 1.000 kg", "1000", "KG", "0.40", "15"),
    ("Tambor 200 L", "200", "L", "0.30", "4"),
    ("Bombona 50 L", "50", "L", "0.30", "1.5"),
]


def criar(apps, schema_editor):
    Tipo = apps.get_model("servicos", "TipoRecipiente")
    for nome, cap, un, preco, tempo in INICIAIS:
        Tipo.objects.get_or_create(nome=nome, defaults={
            "capacidade": Decimal(cap), "unidade": un, "preco_kg": Decimal(preco), "tempo_padrao_min": Decimal(tempo),
        })


class Migration(migrations.Migration):
    dependencies = [("servicos", "0001_initial")]
    operations = [migrations.RunPython(criar, migrations.RunPython.noop)]
