from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("rotas", "0002_tipos_documento")]
    operations = [
        migrations.RenameField("loja", "frete_envio", "frete_accelo"),
        migrations.RenameField("loja", "frete_retorno", "frete_atego"),
        migrations.CreateModel(
            name="Veiculo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("placa", models.CharField(help_text="Sem traço, ex.: ABC1234.", max_length=10, unique=True)),
                ("tipo", models.CharField(choices=[("accelo", "Accelo (2 eixos)"), ("atego", "Atego (3 eixos)")], max_length=10)),
                ("descricao", models.CharField(blank=True, default="", max_length=60)),
            ],
            options={"ordering": ["placa"], "verbose_name": "veículo"},
        ),
    ]
