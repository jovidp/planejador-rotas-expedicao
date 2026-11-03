"""API de leitura para outros sistemas, com chave (Cadastro → Chaves de API). Só consulta: nada é alterado por aqui.

Chave no cabeçalho "Authorization: Bearer <chave>" ou "X-Api-Key: <chave>". Guia em LEIAME.md.
"""
import hashlib
from functools import wraps

from django.contrib.auth.decorators import login_not_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .api import _data, _rotas, rota_json
from .models import ChaveApi, Loja, Rota


def resumo(chave):
    return hashlib.sha256(chave.encode()).hexdigest()


def com_chave(view):
    @login_not_required
    @require_GET
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        chave = request.headers.get("X-Api-Key") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        c = ChaveApi.objects.filter(resumo=resumo(chave), ativa=True).first() if chave else None
        if not c:
            return JsonResponse({"erro": "Chave de API inválida ou desligada."}, status=401)
        ChaveApi.objects.filter(pk=c.pk).update(usada_em=timezone.now())
        try:
            return view(request, *args, **kwargs)
        except ValueError as e:
            return JsonResponse({"erro": str(e)}, status=400)
    return wrapper


def _rota(r):
    d = rota_json(r)
    del d["historico"]  # quem fez o quê fica só na tela
    return d


@com_chave
def rotas(request):
    """?data=AAAA-MM-DD (padrão: hoje). Rotas do dia, ativas e canceladas, com as paradas."""
    dia = _data(request.GET["data"]) if request.GET.get("data") else timezone.localdate()
    return JsonResponse({"data": dia.isoformat(), "rotas": [_rota(r) for r in _rotas().filter(data=dia).order_by("ordem", "id")]})


@com_chave
def rota(request, pk):
    r = _rotas().filter(pk=pk).first()
    return JsonResponse(_rota(r)) if r else JsonResponse({"erro": "Rota não encontrada."}, status=404)


@com_chave
def lojas(request):
    return JsonResponse({"lojas": [{"nome": l.nome, "pes": l.pes, "emp": l.emp, "horario": l.horario,
                                    "rota": l.rota_padrao.nome if l.rota_padrao else "", "ordem_na_rota": l.ordem_na_rota}
                                   for l in Loja.objects.select_related("rota_padrao")]})
