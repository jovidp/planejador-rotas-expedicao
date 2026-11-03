from django.db import migrations

# Frota informada pelo usuário em 23/10/2026. Novos carros entram pela aba Veículos.
ATEGO = ["ATG1A01", "ATG2B02", "ATG3C03", "ABC1234", "ATG5E05"]
ACCELO = ["ACL1A01", "ACL2B02", "ACL3C03", "BRA2E19", "ACL5E05", "ACL6F06", "ACL7G07"]


def cadastrar(apps, schema_editor):
    Veiculo = apps.get_model("rotas", "Veiculo")
    for tipo, placas in (("atego", ATEGO), ("accelo", ACCELO)):
        for placa in placas:
            Veiculo.objects.get_or_create(placa=placa, defaults={"tipo": tipo})


class Migration(migrations.Migration):
    dependencies = [("rotas", "0004_frete_accelo_atego")]
    operations = [migrations.RunPython(cadastrar, migrations.RunPython.noop)]
