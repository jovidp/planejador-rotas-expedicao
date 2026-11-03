"""Painel do mês (/painel/): números das rotas já gravadas no banco, sem chamar a TOTVS."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from .models import Parada, Rota

DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def _int(v):
    try:
        return int(str(v).strip() or 0)
    except ValueError:  # RT/CX é texto livre: o que não for número fica de fora
        return 0


def mes_anterior_e_seguinte(ini):
    ant = date(ini.year - (ini.month == 1), ini.month - 1 or 12, 1)
    seg = date(ini.year + (ini.month == 12), ini.month % 12 + 1, 1)
    return ant, seg


def resumo(ini):
    """ini = dia 1 do mês."""
    _, fim = mes_anterior_e_seguinte(ini)
    rotas = Rota.objects.filter(data__gte=ini, data__lt=fim)
    paradas = (Parada.objects.filter(rota__in=rotas.filter(status=Rota.ATIVA)).exclude(pes="")
               .select_related("rota").prefetch_related("documentos"))
    lojas = defaultdict(lambda: {"entregas": 0, "volumes": 0, "rt": 0, "cx": 0, "frete": Decimal(0)})
    motoristas = defaultdict(lambda: {"rotas": set(), "entregas": 0})
    tot = {"entregas": 0, "volumes": 0, "rt": 0, "cx": 0, "frete": Decimal(0)}
    for p in paradas:
        l = lojas[p.loja or p.pes]
        v = {"entregas": 1, "volumes": sum(d.volumes or 0 for d in p.documentos.all()), "rt": _int(p.rt), "cx": _int(p.cx),
             "frete": (p.frete_envio or 0) + (p.frete_retorno or 0)}
        for k in v:
            l[k] += v[k]; tot[k] += v[k]
        m = motoristas[p.rota.motorista.strip().upper() or "SEM MOTORISTA"]
        m["rotas"].add(p.rota_id); m["entregas"] += 1
    por_dia = [0] * 7
    for d in rotas.filter(status=Rota.ATIVA).values_list("data", flat=True):
        por_dia[d.weekday()] += 1
    maior = max(por_dia) or 1
    return {
        "ativas": rotas.filter(status=Rota.ATIVA).count(), "canceladas": rotas.filter(status=Rota.CANCELADA).count(),
        "tot": tot, "frete_medio": tot["frete"] / tot["entregas"] if tot["entregas"] else 0,
        "dias": [{"nome": n, "rotas": c, "pct": 100 * c // maior} for n, c in zip(DIAS, por_dia)],
        "lojas": sorted(({"nome": n, **v} for n, v in lojas.items()), key=lambda x: (-x["frete"], -x["entregas"])),
        "motoristas": sorted(({"nome": n, "rotas": len(v["rotas"]), "entregas": v["entregas"]} for n, v in motoristas.items()),
                             key=lambda x: -x["entregas"]),
    }
