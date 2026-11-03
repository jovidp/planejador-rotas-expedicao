from django.contrib import admin

import secrets

from django.contrib import messages

from .api_externa import resumo
from .models import ChaveApi, Historico, Loja, RegraEntrega, Rota, RotaPadrao, TipoDocumento, Veiculo

admin.site.site_header = "Rotas · Expedição ADM"


class LojaInline(admin.TabularInline):
    model = Loja
    fields = ["ordem_na_rota", "nome", "pes", "emp", "horario", "frete_accelo", "frete_atego"]
    extra = 0


@admin.register(RotaPadrao)
class RotaPadraoAdmin(admin.ModelAdmin):
    list_display = ["nome", "saida", "ativa"]
    inlines = [LojaInline]


@admin.register(Loja)
class LojaAdmin(admin.ModelAdmin):
    list_display = ["nome", "pes", "emp", "rota_padrao", "horario", "ramal", "celular_oi"]
    list_filter = ["rota_padrao"]
    search_fields = ["nome", "pes", "emp"]


@admin.register(RegraEntrega)
class RegraEntregaAdmin(admin.ModelAdmin):
    list_display = ["emp", "carrinho_4", "carrinho_2", "sem_carrinho_apos", "horario_docas", "autorizacao_previa", "mes_referencia"]
    search_fields = ["emp"]


@admin.register(TipoDocumento)
class TipoDocumentoAdmin(admin.ModelAdmin):
    list_display = ["nome", "codigo", "ordem", "eh_carga"]


@admin.register(Rota)
class RotaAdmin(admin.ModelAdmin):
    list_display = ["data", "nome", "status", "fora_do_planejamento", "remarcacoes"]
    list_filter = ["status", "data"]


@admin.register(Historico)
class HistoricoAdmin(admin.ModelAdmin):
    list_display = ["quando", "usuario", "rota", "acao"]
    search_fields = ["acao", "usuario"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Veiculo)
class VeiculoAdmin(admin.ModelAdmin):
    list_display = ["placa", "tipo", "descricao"]
    list_filter = ["tipo"]
    search_fields = ["placa", "descricao"]


@admin.register(ChaveApi)
class ChaveApiAdmin(admin.ModelAdmin):
    list_display = ["nome", "ativa", "criada_em", "usada_em"]
    readonly_fields = ["criada_em", "usada_em"]

    def save_model(self, request, obj, form, change):
        if not change:  # a chave aparece só agora: o banco guarda só o resumo
            chave = secrets.token_urlsafe(32)
            obj.resumo = resumo(chave)
            messages.warning(request, f"Chave de {obj.nome}: {chave} (copie agora; ela não aparece de novo)")
        super().save_model(request, obj, form, change)
