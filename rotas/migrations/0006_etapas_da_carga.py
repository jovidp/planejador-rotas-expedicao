from django.db import migrations, models


def converter(apps, schema_editor):
    """Regra antiga: carga com atendimento confirmado (ou com NF) = pronta para a NF. Na nova, isso é 'Atendida'."""
    Documento = apps.get_model("rotas", "Documento")
    for d in Documento.objects.filter(tipo__eh_carga=True).exclude(numero="").select_related("parada"):
        if d.atendido_por or d.parada.nf_feita:
            quando = d.atendido_em or d.parada.nf_em
            quem = d.atendido_por or d.parada.nf_por
            d.expedida_por = d.atendido_por = d.concluido_por = quem
            d.expedida_em = d.atendido_em = d.concluido_em = quando
            d.save()


class Migration(migrations.Migration):
    dependencies = [("rotas", "0005_frota")]
    operations = [
        migrations.AddField("documento", "expedida_por", models.CharField(blank=True, default="", max_length=60)),
        migrations.AddField("documento", "expedida_em", models.DateTimeField(blank=True, null=True)),
        migrations.AddField("documento", "concluido_por", models.CharField(blank=True, default="", max_length=60)),
        migrations.AddField("documento", "concluido_em", models.DateTimeField(blank=True, null=True)),
        migrations.RunPython(converter, migrations.RunPython.noop),
    ]
