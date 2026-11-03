from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rotas", "0014_nota_retirada"),
    ]

    operations = [
        migrations.AddField(
            model_name="parada",
            name="cte_ctrc",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="parada",
            name="cte_situacao",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="parada",
            name="cte_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
