# Sistema de Rotas · Expedição ADM

Planejamento semanal das rotas, que substitui a planilha do OnlyOffice.

## Primeira instalação (no servidor)

Requisito: Python 3.12 ou mais novo, instalado com a opção "Add to PATH".

1. Copie a pasta `sistema` para o servidor, por exemplo `C:\rotas`.
2. Rode `atualizar.bat`. Ele cria o ambiente, instala o Django e cria o banco.
3. Crie o primeiro administrador e importe o cadastro de rotas e lojas:
   ```
   .venv\Scripts\python manage.py createsuperuser
   .venv\Scripts\python manage.py importar_cadastro
   ```
4. Rode `rodar.bat`. Os usuários abrem `http://NOME-DO-SERVIDOR:8000`.
   - Para o sistema subir junto com o Windows, crie no **Agendador de Tarefas** uma tarefa "Ao iniciar o computador" que execute o `rodar.bat`.
   - Libere a porta 8000 no firewall do servidor.

## Usuários e cadastro

- **Usuários:** são criados em `/admin` → Usuários. O nome que aparece em "atendeu", "NF por" e no histórico é o campo **Primeiro nome**, ou o usuário quando esse campo está vazio.
- **Administrador:** marque "Membro da equipe" e "Superusuário". Ele vê o link **Cadastro** no topo da tela.
- **Rotas, lojas, regras de entrega e tipos de documento:** são mantidos em `/admin`. Mudar o cadastro não altera as rotas que já foram criadas.

## Lançar uma atualização

1. Pare o `rodar.bat` (feche a janela ou aperte Ctrl+C).
2. Copie o código novo por cima da pasta, ou rode `git pull`. **Não apague** `db.sqlite3`, `chave.txt` e `backups\`.
3. Rode `atualizar.bat`. Antes de mudar qualquer coisa, ele faz o backup do banco em `backups\`.
4. Rode `rodar.bat` de novo. Os usuários só precisam apertar F5.

Se algo der errado, pare o sistema, copie o último `backups\db-*.sqlite3` por cima de `db.sqlite3` e volte o código para a versão anterior.

## Backup diário

Crie no Agendador de Tarefas uma tarefa diária que copie o banco:

```
cmd /c copy /y C:\rotas\db.sqlite3 "\\SERVIDOR-DE-ARQUIVOS\backup\rotas\db-%date:~6,4%%date:~3,2%%date:~0,2%.sqlite3"
```

## Desenvolvimento

```
set ROTAS_DEBUG=1
.venv\Scripts\python manage.py runserver
.venv\Scripts\python manage.py test rotas
```

## Integração com a TOTVS Moda

A cada 30 segundos, e na hora em que um número é digitado, o sistema consulta na API V2 da TOTVS as transações lançadas nas colunas 434, 3386, Almoxarifado (5588) e Sacola (4437 ou 538). A situação de cada transação (Em andamento, Liberada p/ faturamento etc.) aparece embaixo do número, como "TOTVS: Em andamento". Quando a TOTVS dá a transação como **Atendida**, o atendimento é confirmado sozinho, em nome de **TOTVS**. A consulta usa a empresa do CD (209), a operação da coluna, o número digitado e a loja como cliente ("100000" na frente do PES: 556 → 100000556; a ILHA, de PES 4147, é a pessoa 4147). Como a TOTVS repete os números com os anos, só vale a transação mais recente lançada até 30 dias antes da data da rota. Clicar no número continua funcionando. Se alguém desfizer à mão, a TOTVS não marca de novo aquele número.

A integração só liga com todas estas variáveis de ambiente no servidor. Nesta máquina, use `setx NOME "valor"` e reinicie o sistema. Na Render, use Environment.

| Variável | O que é |
|---|---|
| `TOTVS_URL` | Endereço da API da empresa, ex.: `https://totvs.exemplo.com.br` |
| `TOTVS_CLIENT_ID` / `TOTVS_CLIENT_SECRET` | Vêm com a licença da API V2 |
| `TOTVS_USUARIO` / `TOTVS_SENHA` | Usuário do TOTVS Moda (ADMFM026) com acesso à empresa do CD |
| `TOTVS_EMPRESA` | Código da empresa do CD. Para mais de uma, separe por vírgula |

Erros de conexão ou de senha vão para o `servidor.log`, e a consulta tenta de novo na rodada seguinte.

## Limites conhecidos

- **Copiar imagem:** o navegador só deixa o site copiar sozinho em `https` ou em `localhost`. Pela rede (`http://servidor:8000`), o botão mostra a imagem pronta e a pessoa copia com o botão direito → "Copiar imagem". Para copiar com um clique em todas as máquinas, a TI precisa pôr um certificado (https) na frente do sistema, ou liberar o endereço pela política `OverrideSecurityRestrictionsOnInsecureOrigin` do Chrome/Edge.
- **CSS e JS:** quem serve é o próprio sistema. Se a tela ficar lenta com muitos usuários, a TI pode colocar um IIS ou nginx na frente.

## Na nuvem (Render)

O mesmo código roda na Render (site) com o banco Postgres na Supabase. Lá não existe instalador: cada `git push` publica sozinho, e a tela de quem está usando recarrega na versão nova.

1. Na Supabase: crie o projeto (região **East US / N. Virginia**, perto da Render) e anote a senha do banco. Em **Connect → Session pooler**, copie a URI (porta 5432) e troque `[YOUR-PASSWORD]` pela senha.
2. Em render.com: **New → Blueprint**, escolha este repositório do GitHub.
3. Preencha `DATABASE_URL` (a URI da Supabase), `DJANGO_SUPERUSER_USERNAME` e `DJANGO_SUPERUSER_PASSWORD` (primeiro administrador) e confirme.
3. Endereço: `https://seu-app.onrender.com` (ou o nome que a Render der).

Diferenças da versão desta máquina: o login com a senha do Windows não funciona fora da rede da empresa (cada pessoa usa a senha do sistema, criada em `/admin`); o banco começa vazio, só com o cadastro de rotas, lojas e frota.

## API para outros sistemas (só leitura)

1. Em **Cadastro → Chaves de API → Adicionar**, dê o nome do sistema e salve. A chave aparece **uma vez** no topo da tela: copie.
   Para cortar o acesso, desmarque **Ativa** (ou apague a chave).
2. O outro sistema manda a chave em todo pedido, no cabeçalho `Authorization: Bearer <chave>` (ou `X-Api-Key: <chave>`).

| Pedido (GET) | O que devolve |
|---|---|
| `/api/v1/rotas/?data=2026-10-27` | rotas do dia (sem `data`: hoje), com motorista, veículo e as paradas: loja, PES, cargas WMS com a etapa, transações com quem atendeu e a situação na TOTVS, aviso dos pedidos |
| `/api/v1/rotas/<id>/` | uma rota |
| `/api/v1/lojas/` | cadastro de lojas: nome, PES, EMP, horário e a rota de cada uma |

Exemplo: `curl -H "Authorization: Bearer <chave>" http://<servidor>:8000/api/v1/rotas/`.
Erros: 401 chave errada ou desligada, 400 data inválida, 404 rota não existe. O histórico das rotas não sai pela API.
