"""O que muda conforme onde o sistema roda: nesta máquina (Windows, SQLite) ou na nuvem (Render + Postgres/Supabase)."""
from urllib.parse import parse_qsl, unquote, urlparse


def banco(url, padrao):
    """DATABASE_URL (postgres://usuario:senha@host:porta/nome?sslmode=...) → DATABASES['default']; sem URL, o SQLite local."""
    if not url:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": padrao}
    u = urlparse(url)
    if u.scheme not in ("postgres", "postgresql"):
        raise ValueError(f"DATABASE_URL não é Postgres: {u.scheme}")
    b = {"ENGINE": "django.db.backends.postgresql", "NAME": u.path.lstrip("/"), "USER": unquote(u.username or ""),
         "PASSWORD": unquote(u.password or ""), "HOST": u.hostname or "", "PORT": str(u.port or 5432), "CONN_MAX_AGE": 60}
    opcoes = dict(parse_qsl(u.query))
    if (u.hostname or "").endswith(".supabase.com"):
        opcoes.setdefault("sslmode", "require")  # banco fora da Render: conexão sempre criptografada
    if opcoes:
        b["OPTIONS"] = opcoes
    return b
