import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rotas", "0013_chave_api"),
    ]

    operations = [
        migrations.CreateModel(
            name="NotaRetirada",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("chave", models.CharField(max_length=44)),
                ("numero", models.PositiveIntegerField()),
                ("serie", models.CharField(blank=True, default="", max_length=5)),
                ("emissao", models.DateField()),
                ("operacao", models.PositiveIntegerField()),
                ("nome_operacao", models.CharField(blank=True, default="", max_length=100)),
                ("volumes", models.PositiveIntegerField(blank=True, null=True)),
                ("valor", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("parada", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notas_retirada", to="rotas.parada")),
            ],
            options={
                "verbose_name": "nota de retirada",
                "verbose_name_plural": "notas de retirada",
                "ordering": ["emissao", "numero"],
                "unique_together": {("parada", "chave")},
            },
        ),
    ]
