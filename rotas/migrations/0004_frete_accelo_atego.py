import json
from decimal import Decimal
from pathlib import Path

from django.db import migrations

ARQUIVO = Path(__file__).resolve().parents[1] / "dados" / "cadastro.json"


def preencher(apps, schema_editor):
    """Instalações que já existem: troca o frete antigo (KM×9) pela tabela Accelo/Atego de 23/10."""
    Loja = apps.get_model("rotas", "Loja")
    frete = json.loads(ARQUIVO.read_text(encoding="utf-8"))["frete"]
    for loja in Loja.objects.filter(nome__in=frete):
        accelo, atego = frete[loja.nome]
        loja.frete_accelo, loja.frete_atego = Decimal(accelo), Decimal(atego)
        loja.save(update_fields=["frete_accelo", "frete_atego"])


class Migration(migrations.Migration):
    dependencies = [("rotas", "0003_veiculo_frete_por_tipo")]
    operations = [migrations.RunPython(preencher, migrations.RunPython.noop)]
