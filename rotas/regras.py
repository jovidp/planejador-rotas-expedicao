import re
from decimal import Decimal, InvalidOperation

_HORA = re.compile(r"^(\d{1,2})(?:\s*[:h.,]\s*(\d{1,2})?)?\s*h?$")
_HORA_JUNTA = re.compile(r"^(\d{1,2})(\d{2})$")


def fmt_hora(v):
    """'730', '7h30', '7.30' → '07:30'; '7' → '07:00'. Texto que não é horário fica como foi digitado."""
    t = (v or "").strip()
    m = _HORA.match(t.lower()) or _HORA_JUNTA.match(t)
    if not m:
        return t
    h = int(m[1])
    mi = m[2] or "0"
    mi = int(mi) * 10 if len(mi) == 1 and m[2] else int(mi)
    if h > 23 or mi > 59:
        return t
    return f"{h:02d}:{mi:02d}"


def parse_valor(v):
    """'1.369,11', 'R$ 1.369,11' ou '1369.11' → Decimal('1369.11'); vazio ou inválido → None."""
    t = re.sub(r"[^\d,.-]", "", v or "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return Decimal(t) if t else None
    except InvalidOperation:
        return None


def pessoa_da_loja(pes):
    """A loja como pessoa (cliente) na TOTVS e na Alcis: "100000" na frente do PES de 3 dígitos (556 → 100000556);
    PES de 4 dígitos já é a pessoa (ILHA: 4147)."""
    s = str(pes)
    return int(s) if len(s) >= 4 else int("100000" + s.zfill(3))


def pes_da_pessoa(cod):
    """O contrário: 100000457 → "457"; 4147 → "4147"; outro formato → None."""
    s = str(cod or "").strip()
    if not s.isdigit():
        return None
    if len(s) == 9 and s.startswith("100000"):
        return str(int(s[6:]))
    return s if 4 <= len(s) <= 5 else None


def nome_usuario(user):
    return (user.first_name or user.username).upper()


ETAPAS = ["criada", "expedida", "em_atendimento", "atendida"]


def etapa_carga(doc):
    """Carga criada (só o número) → expedida → em atendimento (quem pegou) → atendida."""
    if doc.concluido_em:
        return "atendida"
    if doc.atendido_por:
        return "em_atendimento"
    if doc.expedida_em:
        return "expedida"
    return "criada"


def status_parada(parada):
    """sem_carga → criada → expedida → em_atendimento → atendida (a mais atrasada das cargas da loja).
    None só quando a parada não tem PES (FÁBRICA); loja sem nada lançado já começa "sem carga"."""
    docs = [d for d in parada.documentos.all() if d.numero]
    if not parada.pes:
        return None
    cargas = [d for d in docs if d.tipo.eh_carga]
    if not cargas:
        return "sem_carga"
    return min((etapa_carga(d) for d in cargas), key=ETAPAS.index)
