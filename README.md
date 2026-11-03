# Rotas · Expedição

Sistema que planeja e acompanha as rotas diárias de entrega de um centro de
distribuição para ~90 lojas no estado do Rio de Janeiro. Substituiu uma planilha
compartilhada e passou a conversar sozinho com o ERP, o WMS e o sistema da
transportadora, tirando da equipe o trabalho de copiar números de um lugar para
outro.

> Versão pública para portfólio: lojas, valores, telefones, placas, pessoas e
> endereços de servidor foram trocados por dados fictícios.

## O problema

A expedição montava a semana de rotas numa planilha: quais lojas cada caminhão
atende, em que ordem, com quais cargas e notas. Cada número vinha de um sistema
diferente (TOTVS, WMS, SSW) e era digitado à mão, e o status de cada entrega
dependia de alguém perguntar.

## O que o sistema faz

- **Planejamento semanal** das rotas, com ordem das paradas, horários, motorista,
  veículo e frete calculado pelo tipo de caminhão.
- **Integração com o ERP TOTVS Moda (API V2)**: a cada 30 s consulta a situação de
  cada transação digitada e marca o atendimento sozinho quando a TOTVS conclui.
- **Integração com o WMS (Alcis)**: encontra as cargas novas e lança cada uma na
  próxima rota da loja certa.
- **Emissão de CT-e no SSW** com automação de navegador (Playwright): agrupa as
  notas da loja, calcula peso e frete, grava, envia à SEFAZ e guarda o DACTE em PDF,
  com travas para não emitir nota duplicada.
- **DANFE a partir do XML** quando o ERP não gera o PDF.
- **Documentos da rota** (capa para o motorista, relatório de entrega) em PNG e
  PDF, prontos para WhatsApp e e-mail.
- **Painel do mês** com os números das rotas gravadas.
- **API REST somente leitura** com chave por sistema (`Authorization: Bearer`).
- Login com a senha do Windows (domínio da empresa) ou senha própria do sistema.

## Destaques técnicos

- **Django 5** com regras de negócio em módulos próprios (`acoes.py`, `regras.py`)
  e integrações isoladas (`totvs.py`, `alcis.py`, `ssw.py`, `retiradas.py`).
- **212 testes automatizados**: as integrações externas são simuladas com `mock`,
  então a suíte inteira roda offline em menos de 1 minuto.
- **Dois jeitos de rodar**: no servidor Windows da empresa (SQLite, instalador
  `.exe`, backup diário) ou na nuvem (Render + PostgreSQL na Supabase, deploy a
  cada `git push`), com o mesmo código.
- Fila em segundo plano para as emissões no SSW, que levam minutos, sem travar a
  tela de quem pediu.
- A tela recarrega sozinha quando sai uma versão nova.
- Credenciais só por variável de ambiente; nenhuma integração liga sem elas.

## Rodando localmente

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
set ROTAS_DEBUG=1                 # Linux/macOS: export ROTAS_DEBUG=1
python manage.py migrate
python manage.py importar_cadastro   # rotas e lojas de exemplo
python manage.py createsuperuser
python manage.py runserver
```

Testes:

```bash
python manage.py test
```

As integrações (TOTVS, Alcis, SSW) ficam desligadas até as variáveis de ambiente
serem preenchidas. Detalhes de instalação, integrações e API: [LEIAME.md](LEIAME.md).

## Tecnologias

Python · Django · PostgreSQL / SQLite · Playwright · JavaScript · Render · Supabase · API TOTVS Moda
