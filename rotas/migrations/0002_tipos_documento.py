from django.db import migrations

TIPOS = [("t434", "434", False), ("t3386", "3386", False), ("almox", "Almoxarifado", False), ("sacola", "Sacola", False), ("wms", "Carga WMS", True)]


def criar(apps, schema_editor):
    TipoDocumento = apps.get_model("rotas", "TipoDocumento")
    for i, (codigo, nome, carga) in enumerate(TIPOS):
        TipoDocumento.objects.get_or_create(codigo=codigo, defaults={"nome": nome, "ordem": i, "eh_carga": carga})


class Migration(migrations.Migration):
    dependencies = [("rotas", "0001_inicial")]
    operations = [migrations.RunPython(criar, migrations.RunPython.noop)]
