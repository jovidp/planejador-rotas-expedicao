from django.conf import settings
from django.db import models


def texto(max_length=100):
    return models.CharField(max_length=max_length, blank=True, default="")


def valor():
    return models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)


# ---------- cadastro ----------

class TipoDocumento(models.Model):
    codigo = models.SlugField(unique=True)
    nome = models.CharField(max_length=50)
    ordem = models.PositiveIntegerField(default=0)
    eh_carga = models.BooleanField("é carga WMS", default=False)

    class Meta:
        ordering = ["ordem"]
        verbose_name = "tipo de documento"
        verbose_name_plural = "tipos de documento"

    def __str__(self):
        return self.nome


class RotaPadrao(models.Model):
    nome = models.CharField(max_length=60, unique=True)
    saida = texto(10)
    variacoes = models.TextField(blank=True, default="", help_text="Uma variação por linha.")
    ativa = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "rota do cadastro"
        verbose_name_plural = "rotas do cadastro"

    def __str__(self):
        return self.nome


class Loja(models.Model):
    nome = models.CharField(max_length=60, unique=True)
    pes = texto(10)
    emp = texto(10)
    horario = texto(40)
    frete_accelo = valor()  # frete de envio com caminhão 2 eixos; o retorno é o mesmo valor
    frete_atego = valor()  # 3 eixos
    ramal = texto(20)
    celular_oi = texto(20)
    rota_padrao = models.ForeignKey(RotaPadrao, null=True, blank=True, on_delete=models.SET_NULL, related_name="lojas")
    ordem_na_rota = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["rota_padrao__nome", "ordem_na_rota"]

    def __str__(self):
        return self.nome


class Veiculo(models.Model):
    ACCELO, ATEGO = "accelo", "atego"

    placa = models.CharField(max_length=10, unique=True, help_text="Sem traço, ex.: ABC1234.")
    tipo = models.CharField(max_length=10, choices=[(ACCELO, "Accelo (2 eixos)"), (ATEGO, "Atego (3 eixos)")])
    descricao = texto(60)

    class Meta:
        ordering = ["placa"]
        verbose_name = "veículo"

    def save(self, *args, **kwargs):
        self.placa = normalizar_placa(self.placa)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.placa} ({self.get_tipo_display()})"


def normalizar_placa(v):
    return "".join(c for c in (v or "").upper() if c.isalnum())


class RegraEntrega(models.Model):
    emp = models.CharField("EMP", max_length=10, unique=True)
    carrinho_4 = texto(120)
    carrinho_2 = texto(120)
    sem_carrinho_apos = texto(20)
    horario_docas = texto(120)
    autorizacao_previa = texto(20)
    observacao = models.TextField(blank=True, default="")
    mes_referencia = texto(20)

    class Meta:
        ordering = ["emp"]
        verbose_name = "regra de entrega"
        verbose_name_plural = "regras de entrega"

    def __str__(self):
        return f"EMP {self.emp}"


# ---------- planejamento ----------

class Rota(models.Model):
    ATIVA, CANCELADA = "ativa", "cancelada"

    data = models.DateField(db_index=True)
    nome = models.CharField(max_length=60)
    saida = texto(10)
    lacre = texto(30)
    motorista = texto(60)
    veiculo = texto(20)
    ajudantes = texto(120)
    rastreador = texto(20)
    status = models.CharField(max_length=10, default=ATIVA, choices=[(ATIVA, "Ativa"), (CANCELADA, "Cancelada")])
    fora_do_planejamento = models.BooleanField(default=False)
    remarcacoes = models.PositiveIntegerField(default=0)
    ordem = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["data", "ordem", "id"]

    def __str__(self):
        return f"{self.nome} {self.data:%d/%m}"


class Parada(models.Model):
    rota = models.ForeignKey(Rota, on_delete=models.CASCADE, related_name="paradas")
    ordem = models.PositiveIntegerField(default=0)
    pes = texto(10)
    loja = texto(60)
    emp = texto(10)
    horario = texto(60)
    rt = texto(10)
    cx = texto(10)
    # retiradas confirmadas com um clique no volume (quem e quando), como o atendimento das transações
    rt_por = texto(60)
    rt_em = models.DateTimeField(null=True, blank=True)
    cx_por = texto(60)
    cx_em = models.DateTimeField(null=True, blank=True)
    frete_envio = valor()
    frete_retorno = valor()
    # CT-e da retirada feito no SSW pelo botão da página de retiradas (rotas/ssw.py)
    cte_ctrc = texto(20)  # "?" = pode ter gravado e não se sabe o número: conferir no SSW antes de tentar de novo
    cte_situacao = texto(200)
    cte_em = models.DateTimeField(null=True, blank=True)
    cte_por = texto(150)  # usuário do sistema que pediu: o SSW entra com o login dele (LoginSSW)
    # CT-es da ida (rotas/ssw.py, página /ctes/): um das NFs da 434/3386 (empresa 209) e um da sacola (empresa 1, outro CNPJ)
    ida_ctrc = texto(20)
    ida_situacao = texto(200)
    ida_em = models.DateTimeField(null=True, blank=True)
    ida_por = texto(150)
    sacola_ctrc = texto(20)
    sacola_situacao = texto(200)
    sacola_em = models.DateTimeField(null=True, blank=True)
    sacola_por = texto(150)

    class Meta:
        ordering = ["ordem"]

    def __str__(self):
        return f"{self.ordem}º {self.loja}"


class Documento(models.Model):
    parada = models.ForeignKey(Parada, on_delete=models.CASCADE, related_name="documentos")
    tipo = models.ForeignKey(TipoDocumento, on_delete=models.PROTECT)
    numero = texto(60)
    atendido_por = texto(60)  # na carga: quem iniciou o atendimento
    atendido_em = models.DateTimeField(null=True, blank=True)
    # só na carga WMS: criada → expedida → em atendimento (atendido_*) → atendida (concluido_*)
    expedida_por = texto(60)
    expedida_em = models.DateTimeField(null=True, blank=True)
    concluido_por = texto(60)
    concluido_em = models.DateTimeField(null=True, blank=True)
    situacao_totvs = texto(40)  # última situação lida na API da TOTVS (rotas/totvs.py)
    volumes = models.PositiveIntegerField(null=True, blank=True)  # da NF da 434/3386 na TOTVS ou dos volumes da carga na Alcis
    # só na carga WMS: situação dos pedidos dela na TOTVS (rotas/pedidos.py)
    aviso_pedidos = texto(100)  # ex.: "2 bloqueados, 1 cancelado"
    pedidos_detalhe = models.TextField(blank=True, default="")  # todos os pedidos, um por um, por situação
    pedidos_fase = texto(10)  # "expedida" (conferindo) ou "atendida" (atendida no PEDFM099: não confere mais)

    class Meta:
        ordering = ["id"]  # a loja pode ter várias transações (e cargas WMS) do mesmo tipo


class NotaRetirada(models.Model):
    """NF-e que a loja emitiu para o CD (RT/CX), para os CT-es; o DANFE fica em ROTAS_DADOS/retiradas (rotas/retiradas.py)."""
    parada = models.ForeignKey(Parada, on_delete=models.CASCADE, related_name="notas_retirada")
    chave = models.CharField(max_length=44)
    numero = models.PositiveIntegerField()
    serie = texto(5)
    emissao = models.DateField()
    operacao = models.PositiveIntegerField()
    nome_operacao = texto(100)
    volumes = models.PositiveIntegerField(null=True, blank=True)
    valor = valor()

    class Meta:
        ordering = ["emissao", "numero"]
        unique_together = [("parada", "chave")]
        verbose_name = "nota de retirada"
        verbose_name_plural = "notas de retirada"


class LoginSSW(models.Model):
    """Login de cada pessoa no SSW, para os CT-es saírem no nome dela (e o "meus" da 007 pegar só os dela).
    A senha fica protegida pelo Windows (rotas/windows.py, proteger)."""
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="login_ssw")
    cpf = texto(20)
    login = texto(60)
    senha = models.TextField()

    class Meta:
        verbose_name = "login do SSW"
        verbose_name_plural = "logins do SSW"


class Historico(models.Model):
    usuario = texto(60)
    quando = models.DateTimeField(auto_now_add=True)
    rota = models.ForeignKey(Rota, on_delete=models.CASCADE, related_name="historico")
    parada = models.ForeignKey(Parada, null=True, blank=True, on_delete=models.SET_NULL)
    campo = texto(30)
    anterior = models.TextField(blank=True, default="")
    novo = models.TextField(blank=True, default="")
    acao = models.TextField()

    class Meta:
        ordering = ["id"]
        verbose_name_plural = "histórico"


class VisaoUsuario(models.Model):
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="visao")
    colunas_ocultas = models.JSONField(default=list, blank=True)
    filtro = texto(100)
    apelido = texto(60)  # só o que aparece na tela; quem atendeu continua gravado pelo login/TOTVS
    cor = texto(7)  # cor do nome em "Atendido por", ex.: #22c55e


class ChaveApi(models.Model):
    """Acesso de outro sistema à API de leitura (rotas/api_externa.py). Guarda só o resumo (SHA-256) da chave."""
    nome = models.CharField("sistema", max_length=60)
    resumo = models.CharField(max_length=64, unique=True, editable=False)
    ativa = models.BooleanField(default=True)
    criada_em = models.DateTimeField(auto_now_add=True)
    usada_em = models.DateTimeField("último uso", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "chave de API"
        verbose_name_plural = "chaves de API"

    def __str__(self):
        return self.nome
