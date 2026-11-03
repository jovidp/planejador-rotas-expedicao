import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from rotas.models import Loja, RegraEntrega, RotaPadrao
from rotas.regras import parse_valor

ARQUIVO = Path(__file__).resolve().parents[2] / "dados" / "cadastro.json"


class Command(BaseCommand):
    help = "Importa rotas, lojas, contatos e regras de entrega de rotas/dados/cadastro.json (pode rodar de novo: atualiza)."

    def add_arguments(self, parser):
        parser.add_argument("--se-vazio", action="store_true", help="só importa se ainda não houver lojas (publicação na nuvem)")

    @transaction.atomic
    def handle(self, *args, **opts):
        if opts["se_vazio"] and Loja.objects.exists():
            return
        dados = json.loads(ARQUIVO.read_text(encoding="utf-8"))
        contato, frete = dados["contato"], dados["frete"]
        for nome, r in dados["rotas"].items():
            rp, _ = RotaPadrao.objects.update_or_create(nome=nome, defaults={"saida": r["saida"], "variacoes": "\n".join(r["var"])})
            for i, (loja, pes, emp, horario, *_) in enumerate(r["lojas"], 1):
                ramal, oi = contato.get(loja, ["", ""])
                accelo, atego = frete.get(loja, ["", ""])
                Loja.objects.update_or_create(nome=loja, defaults={
                    "pes": pes, "emp": emp, "horario": horario, "frete_accelo": parse_valor(accelo), "frete_atego": parse_valor(atego),
                    "ramal": ramal, "celular_oi": oi, "rota_padrao": rp, "ordem_na_rota": i})
        for emp, (c4, c2, sem, docas, aut, obs) in dados["docas"].items():
            RegraEntrega.objects.update_or_create(emp=emp, defaults={
                "carrinho_4": c4, "carrinho_2": c2, "sem_carrinho_apos": sem, "horario_docas": docas,
                "autorizacao_previa": aut, "observacao": obs, "mes_referencia": "09/2026"})
        if opts["verbosity"]:
            self.stdout.write(f"{RotaPadrao.objects.count()} rotas, {Loja.objects.count()} lojas, {RegraEntrega.objects.count()} regras de entrega.")
