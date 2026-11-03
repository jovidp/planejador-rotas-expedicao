from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path, re_path
from django.views.static import serve

from rotas import api, api_externa, views



@login_not_required
def service_worker(request):
    # fica na raiz para valer para o site todo
    return serve(request, "sw.js", document_root=settings.BASE_DIR / "rotas" / "static" / "rotas")


@login_not_required
def estaticos(request, path):
    # shortcut: o próprio Django serve CSS/JS no servidor (Waitress); se ficar lento, colocar IIS/nginx na frente.
    return serve(request, path, document_root=settings.STATIC_ROOT)


urlpatterns = [
    path("", views.semana, name="semana"),
    path("retiradas/", views.retiradas, name="retiradas"),
    re_path(r"^retiradas/(?P<chave>\d{44})\.pdf$", views.retirada_pdf, name="retirada_pdf"),
    path("ctes/", views.ctes, name="ctes"),
    path("painel/", views.painel, name="painel"),
    path("ctes/rota/<int:pk>.pdf", views.ctes_rota_pdf, name="ctes_rota_pdf"),
    path("ctes/<int:pk>/<str:tipo>/", views.cte_ida, name="cte_ida"),
    path("ctes/<int:pk>/<str:tipo>.pdf", views.cte_ida_pdf, name="cte_ida_pdf"),
    path("ssw/login/", views.login_ssw, name="login_ssw"),
    path("retiradas/rota/<int:pk>.pdf", views.retiradas_rota_pdf, name="retiradas_rota_pdf"),
    path("retiradas/buscar/", views.retiradas_buscar, name="retiradas_buscar"),
    path("retiradas/cte/<int:pk>/", views.retirada_cte, name="retirada_cte"),
    path("retiradas/cte/<int:pk>.pdf", views.retirada_cte_pdf, name="retirada_cte_pdf"),
    path("api/semana/<str:dia>/", api.semana),
    path("api/dia/<str:dia>/", api.dia),
    path("api/mudancas/", api.mudancas),
    path("api/cadastro/", api.cadastro),
    path("api/visao/", api.visao),
    path("api/planejamento/<str:dia>/", api.planejamento),
    path("api/planejar/", api.planejar),
    path("api/veiculos/", api.veiculos),
    path("api/veiculos/remover/", api.veiculos_remover),
    path("api/cadastro/rota/", api.cadastro_rota),
    path("api/rota/", api.criar_rota),
    path("api/rota/<int:pk>/campo/", api.rota_campo),
    path("api/rota/<int:pk>/mover/", api.rota_mover),
    path("api/rota/<int:pk>/status/", api.rota_status),
    path("api/rota/<int:pk>/parada/", api.rota_parada),
    path("api/rota/<int:pk>/duplicar/", api.rota_duplicar),
    path("api/rota/<int:pk>/excluir/", api.rota_excluir),
    path("api/rota/<int:pk>/inverter/", api.rota_inverter),
    path("api/rota/<int:pk>/capa/", api.capa),
    path("api/rota/<int:pk>/entrega/", api.entrega),
    path("api/parada/<int:pk>/campo/", api.parada_campo),
    path("api/parada/<int:pk>/documento/", api.parada_documento),
    path("api/parada/<int:pk>/retirada/", api.parada_retirada),
    path("api/parada/<int:pk>/ordem/", api.parada_ordem),
    path("api/parada/<int:pk>/remover/", api.parada_remover),
    path("api/documento/<int:pk>/atender/", api.documento_atender),
    path("api/documento/<int:pk>/etapa/", api.documento_etapa),
    path("api/v1/rotas/", api_externa.rotas),  # API de leitura para outros sistemas, com chave
    path("api/v1/rotas/<int:pk>/", api_externa.rota),
    path("api/v1/lojas/", api_externa.lojas),
    path("accounts/login/", views.Entrar.as_view(), name="login"),  # antes do include: com "Manter conectado"
    path("accounts/", include("django.contrib.auth.urls")),
    path("admin/", admin.site.urls),
    re_path(r"^static/(?P<path>.*)$", estaticos),
    path("sw.js", service_worker),
]
