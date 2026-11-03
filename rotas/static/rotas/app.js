'use strict';
// Tela da semana: o mesmo comportamento de docs/demonstracao.html, com os dados vindos da API (/api/...).
const EU = document.body.dataset.usuario, HOJE = document.body.dataset.hoje;
const $ = id => document.getElementById(id);
const week = $('week'), fat = $('fat'), rsum = $('rsum'), colbtn = $('colbtn'), colpop = $('colpop'), q = $('q'), mine = $('mine'),
  routes = $('routes'), addq = $('addq'), addextra = $('addextra'), addgo = $('addgo'), addlojas = $('addlojas');

// ---------- datas ----------
const key = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const parse = k => { const [y, m, d] = k.split('-').map(Number); return new Date(y, m - 1, d) };
const addDays = (k, n) => { const d = parse(k); d.setDate(d.getDate() + n); return key(d) };
const segunda = k => addDays(k, -((parse(k).getDay() + 6) % 7));
const fmt = k => k.slice(8) + '/' + k.slice(5, 7);
const DOW = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'];

const esc = t => String(t ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const money = v => v === '' || v == null ? '' : Number(v).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
// Fluxo da carga: Sem carga → Carga criada (número lançado) → Expedida → Em atendimento (quem pegou) → Atendida
const ETAPAS = ['sem_carga', 'criada', 'expedida', 'em_atendimento', 'atendida'];
const ST = { sem_carga: 'Sem carga', criada: 'Carga criada', expedida: 'Expedida', em_atendimento: 'Em atendimento', atendida: 'Atendida' };
const PROXIMO = { criada: 'Marcar expedida', expedida: 'Iniciar atendimento', em_atendimento: 'Concluir atendimento' };

// ---------- estado ----------
let dia = (() => { try { return sessionStorage.getItem('dia') || HOJE } catch { return HOJE } })();  // reabre no dia em que estava
let semana = segunda(dia), dias = [], rotas = [], tipos = [], cad = { rotas: {}, lojas: {} };
let pessoas = {}, visao = { colunas_ocultas: [], filtro: '', cor: '', apelido: '' }, fatFiltro = null, movendo = null, ultimo = 0, redesenhando = false;
const rota = id => rotas.find(r => r.id == id);
const parada = (r, id) => r.paradas.find(p => p.id == id);

function colunas() {
  const doc = t => ({ k: 'doc:' + t.codigo, l: t.nome, doc: t.codigo, carga: t.eh_carga });
  return [{ k: 'atd', l: 'ATD', fixed: 1 }, { k: 'pes', l: 'PES' }, { k: 'loja', l: 'Lojas', fixed: 1 }, { k: 'emp', l: 'EMP' }, { k: 'horario', l: 'Horário da loja' },
    ...tipos.filter(t => !t.eh_carga).map(doc),
    { k: 'rt', l: 'RT', t: 'Retirada de recolhimento' }, { k: 'cx', l: 'CX', t: 'Retirada de caixa vazia' },
    { k: 'frete_envio', l: 'Frete envio', money: 1 }, { k: 'frete_retorno', l: 'Frete retorno', money: 1 },
    ...tipos.filter(t => t.eh_carga).map(doc)];
}

// ---------- servidor ----------
const csrf = () => (document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/) || [])[1] || '';
async function api(url, dados) {
  const r = await fetch(url, dados === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify(dados)
  });
  if (r.redirected && r.url.includes('/accounts/login/')) { location.href = r.url; throw new Error('Sessão expirada. Entre de novo.') }
  const j = await r.json().catch(() => ({ erro: `Erro ${r.status} no servidor.` }));
  if (!r.ok) throw new Error(j.erro || `Erro ${r.status} no servidor.`);
  return j;
}
async function enviar(url, dados, msg, naCaixa) {
  try { trocarRota((await api(url, dados)).rota); if (msg) toast(msg) }
  catch (e) { naCaixa ? caixa(e.message) : toast(e.message); await carregarDia() }
}
// erro que precisa ser lido (ex.: marcar atendida antes da TOTVS): fica aberto até clicar em Entendi
function caixa(t) { $('avisotxt').textContent = t; $('aviso').showModal() }
$('avisofechar').onclick = () => $('aviso').close();
function trocarRota(r) {
  const i = rotas.findIndex(x => x.id === r.id);
  if (r.data !== dia) { if (i >= 0) rotas.splice(i, 1) } else if (i >= 0) rotas[i] = r; else rotas.push(r);
  carregarSemana(); renderFat(); renderRoutes();
}
async function carregarSemana() {
  try { dias = (await api(`/api/semana/${dia}/`)).dias; renderWeek() } catch (e) { toast(e.message) }
}
async function carregarDia() {
  try { const j = await api(`/api/dia/${dia}/`); rotas = j.rotas; tipos = j.tipos; pessoas = Object.fromEntries(Object.entries(j.pessoas || {}).map(([n, v]) => [nomeDe(n), v])); ultimo = Math.max(ultimo, j.ultimo); render() }
  catch (e) { toast(e.message) }
}
let tvisao;
function salvarVisao() { clearTimeout(tvisao); tvisao = setTimeout(() => api('/api/visao/', visao).catch(e => toast(e.message)), 400) }

// Sugestões de rota e loja (também chamado depois de salvar uma rota do cadastro, em rotascad.js).
function listasCadastro() {
  $('dl-rotas').innerHTML = Object.entries(cad.rotas).map(([k, c]) => `<option value="${esc(k)}">${c.lojas.length} lojas · saída ${esc(c.saida)}</option>`).join('');
  $('dl-lojas').innerHTML = ['FÁBRICA', ...Object.keys(cad.lojas)].map(n => { const l = cad.lojas[n]; return `<option value="${esc(n)}">${l ? `PES ${esc(l.pes) || '—'} · EMP ${esc(l.emp) || '—'} · ${esc(l.rota)}` : ''}</option>` }).join('');
}

// Pedidos da carga (rotas/pedidos.py): os com problema primeiro, em vermelho.
const PED_PROBLEMA = ['Bloqueado', 'Cancelado', 'Parcialmente atendido'];
function abrirPedidos(p, doc) {
  if (!doc) return;
  const ruim = doc.etapa === 'atendida' ? [...PED_PROBLEMA, 'Em andamento'] : PED_PROBLEMA;  // atendida com pedido ainda em andamento
  const grupos = doc.pedidos_detalhe.split('; ').filter(Boolean).map(g => { const [s, ns = ''] = g.split(': '); return [s, ns.split(', ')] })
    .sort((a, b) => ruim.includes(b[0]) - ruim.includes(a[0]));
  $('pedtitle').textContent = `Pedidos da carga ${doc.numero} · ${p.loja}`;
  $('pedmsg').textContent = doc.pedidos ? `Atenção: ${doc.pedidos}. Confira esses pedidos na TOTVS.` : 'Nenhum pedido com problema.';
  $('pedlista').innerHTML = '<thead><tr><th>Pedido</th><th>Situação</th></tr></thead><tbody>'
    + grupos.flatMap(([s, ns]) => ns.map(n => `<tr class="${ruim.includes(s) ? 'ruim' : ''}"><td class="mono">${esc(n)}</td><td>${esc(s)}</td></tr>`)).join('') + '</tbody>';
  $('peds').showModal();
}
$('pedfechar').onclick = () => $('peds').close();

// ---------- render ----------
function renderWeek() {
  let h = `<button class="nav" data-w="-7" aria-label="Semana anterior">‹</button>`;
  for (const { data: k, ativas: n } of dias)
    h += `<button aria-pressed="${k === dia}" data-k="${k}" class="${k === HOJE ? 'hoje' : ''}${n ? '' : ' vazio'}"><span>${DOW[parse(k).getDay()]}</span><b>${fmt(k).slice(0, 2)}</b><i>${n ? n + ' rota' + (n > 1 ? 's' : '') : 'sem rota'}</i></button>`;
  week.innerHTML = h + `<button class="nav" data-w="7" aria-label="Próxima semana">›</button>`
    + `<input type="date" class="cal" value="${dia}" aria-label="Ir para a data" title="Abrir o calendário">`
    + `<button class="rsem" title="Todas as rotas da semana no formato da rota do dia">Rota da semana</button>`;
}
function renderFat() {
  const ativas = rotas.filter(r => r.status === 'ativa'), all = ativas.flatMap(r => r.paradas).map(p => p.status).filter(Boolean);
  const c = Object.fromEntries(ETAPAS.map(s => [s, 0])); all.forEach(x => c[x]++);
  const k = (id, n, l, cls) => `<button class="kpi ${cls}" data-f="${id}" aria-pressed="${fatFiltro === id}"><span class="n">${n}</span><span class="l">${l}</span></button>`;
  fat.innerHTML = k('all', all.length, 'Lojas nas rotas', '') + ETAPAS.map(s => k(s, c[s], ST[s], s)).join('');
  rsum.innerHTML = `<span>Rotas do dia ${fmt(dia)}: <b>${ativas.length}</b> ativas</span><span><b>${rotas.filter(r => r.status === 'cancelada').length}</b> canceladas</span><span><b>${rotas.filter(r => r.remarcacoes).length}</b> remarcadas</span><span><b>${rotas.filter(r => r.fora_do_planejamento).length}</b> fora do planejamento</span>`;
}
function renderCols() {
  const h = visao.colunas_ocultas;
  colpop.innerHTML = `<div class="hint">Só você vê esta configuração.</div>` + colunas().filter(c => !c.fixed).map(c => `<label><input type="checkbox" data-k="${c.k}" ${h.includes(c.k) ? '' : 'checked'}>${esc(c.l)}${c.t ? ' · ' + c.t : ''}</label>`).join('');
  mine.innerHTML = h.length ? `<b>${h.length}</b> coluna(s) oculta(s) só para ${esc(EU)}` : '';
  if (document.activeElement !== q) q.value = visao.filtro;
}
function cell(c, p, r) {
  const at = `data-r="${r.id}" data-p="${p.id}"`, ed = (f, val, dica = '') => `<span contenteditable="plaintext-only" ${at} data-f="${f}"${dica ? ` title="${dica}"` : ''}>${esc(val)}</span>`;
  if (c.k === 'atd') {
    const b = (a, t, l) => `<button data-act="${a}" ${at} title="${l}" aria-label="${l}">${t}</button>`;
    return `<span class="atd">${p.ordem}º</span><span class="acts">${b('up', '↑', 'Subir parada')}${b('down', '↓', 'Descer parada')}${b('del', 'x', 'Remover parada')}</span>`;
  }
  if (c.carga) {  // carga WMS (uma ou mais, separadas por espaço ou /): cada uma com as suas etapas
    const cs = p.docs[c.doc] || [], varias = cs.length > 1;
    let h = ed(c.k, cs.map(t => t.numero).join(' / '), 'Mais de uma carga: separe os números com espaço ou /');
    if (!cs.length) return h + (p.status === 'sem_carga' ? `<span class="chip sem_carga">Sem carga</span>` : '');
    for (const t of cs) {
      const e = t.etapa, quem = { expedida: [t.expedida_por, t.expedida_em], em_atendimento: [t.por, t.em], atendida: [aguardandoNf(t.concluido_por), t.concluido_em] }[e];
      h += `<div class="carga${t.pedidos ? ' problema' : ''}">${varias ? `<b>${esc(t.numero)}</b>` : ''}<span class="chip ${e}">${ST[e]}${quem && quem[0] ? ': ' + esc(nomeDe(quem[0])) + (quem[1] ? ' · ' + quem[1] : '') : ''}</span>`
        + (PROXIMO[e] ? `<button class="atbtn" data-act="avancar" data-doc="${t.id}" ${at}>${PROXIMO[e]}</button>` : '')
        + (e !== 'criada' ? `<button class="voltar" data-act="voltar" data-doc="${t.id}" ${at} title="Desfaz a última etapa (fica no histórico)">Voltar etapa</button>` : '')
        // pedidos da carga na TOTVS (conferidos depois de expedida): aviso em vermelho, todos os pedidos ao passar o mouse
        + (t.pedidos ? `<button class="avisoped" data-act="pedidos" data-doc="${t.id}" ${at} title="Ver os pedidos da carga">Pedidos: ${esc(t.pedidos)}</button>`
          : t.pedidos_detalhe ? `<button class="pedok" data-act="pedidos" data-doc="${t.id}" ${at} title="Ver os pedidos da carga">Pedidos conferidos</button>` : '') + '</div>';
    }
    return h;
  }
  // números clicáveis (verde e negrito = atendido), o "+" (ou o teclado) abre a edição e, embaixo, quem atendeu
  const clicaveis = (botoes, edicao, rodape, dica) => `<span class="nums">${botoes}<button class="maisaj" data-act="editnums" ${at} title="${dica}" aria-label="${dica}">+</button></span>`
    + `<span class="numsed" hidden>${edicao}</span>${rodape ? `<small class="quem">${rodape}</small>` : ''}`;
  if (c.doc) {
    // transações (uma ou várias, separadas por espaço ou /): clicar no número marca o atendimento
    const ds = p.docs[c.doc] || [];
    const h = ed(c.k, ds.map(t => t.numero).join(' / '), 'Mais de uma transação: separe os números com espaço ou /');
    if (!ds.length) return h;
    const num = t => t.por
      ? (naoBate(t) ? `<button class="num ok diverge" data-act="desatender" data-doc="${t.id}" ${at} title="Marcada atendida por ${esc(nomeVisto(t.por))}, mas na TOTVS está ${esc(t.totvs)}. Clique duas vezes para desfazer">${esc(t.numero)}</button>`
        : `<button class="num ok" data-act="desatender" data-doc="${t.id}" ${at} title="Atendida por ${esc(nomeVisto(t.por))}${t.em ? ' às ' + t.em : ''}. Clique duas vezes para desfazer">${esc(t.numero)}</button>`)
      : `<button class="num${cancelada(t) ? ' diverge' : ''}" data-act="atender" data-doc="${t.id}" ${at} title="Clique para marcar como atendida${t.totvs ? ' (TOTVS: ' + esc(t.totvs) + ')' : ''}">${esc(t.numero)}</button>`;
    const quem = {};
    for (const t of ds) if (t.por) (quem[t.por] ??= []).push(t.numero);
    const nomes = Object.entries(quem);
    const rodape = nomes.length === 1 ? `Atendido por ${pinta(nomes[0][0])}` : nomes.map(([n, ns]) => `${pinta(n)}: ${ns.map(esc).join(', ')}`).join(' · ');
    // situação da TOTVS: a da transação única ainda não atendida, a de toda marcada atendida que não bate e a de toda cancelada
    const totvs = ds.filter(t => naoBate(t) || cancelada(t) || (ds.length === 1 && t.totvs && !t.por))
      .map(t => `<span class="totvs${naoBate(t) || cancelada(t) ? ' diverge' : ''}">${ds.length > 1 ? esc(t.numero) + ' ' : ''}TOTVS: ${esc(t.totvs)}</span>`).join('');
    return clicaveis(ds.map(num).join('<span class="sep"> / </span>'), h, rodape, 'Alterar ou acrescentar números') + totvs;
  }
  if (c.k === 'rt' || c.k === 'cx') {
    // retirada: lançado o volume, clicar nele confirma (verde e negrito) e mostra quem confirmou
    const v = p[c.k], por = p[c.k + '_por'], em = p[c.k + '_em'];
    if (!v) return ed(c.k, v);
    const b = por
      ? `<button class="num ok" data-act="desretirada" data-campo="${c.k}" ${at} title="Atendida por ${esc(nomeVisto(por))}${em ? ' às ' + em : ''}. Clique duas vezes para desfazer">${esc(v)}</button>`
      : `<button class="num" data-act="retirada" data-campo="${c.k}" ${at} title="Clique para marcar como atendida">${esc(v)}</button>`;
    return clicaveis(b, ed(c.k, v), por ? `Atendido por ${pinta(por)}` : '', 'Alterar o volume');
  }
  if (c.money) return `<span contenteditable="plaintext-only" ${at} data-f="${c.k}" data-money>${money(p[c.k])}</span>`;
  if (c.k === 'loja') return `<input class="lojain" list="dl-lojas" ${at} value="${esc(p.loja)}" size="${Math.max(12, p.loja.length + 1)}" placeholder="Pesquisar loja" aria-label="Loja da parada ${p.ordem}">`;
  return ed(c.k, p[c.k]);
}
// atendida na TOTVS, mas a NF (de onde vem o nome de quem atendeu) ainda não saiu: o sistema grava "TOTVS" e troca pelo nome quando acha
const aguardandoNf = nome => nome === 'TOTVS' ? 'aguardando NF' : nome;
// login do Windows sem nome no cadastro chega "BEATRIZ.SANTOS"; a TOTVS manda "BEATRIZ SANTOS": a mesma pessoa, escrita igual
// nome com a cor que a pessoa escolheu (cor validada no servidor: só #rrggbb)
// login (BEATRIZ.SANTOS) e TOTVS (BEATRIZ SANTOS) caem na mesma pessoa; o apelido muda só a tela
const nomeVisto = por => pessoas[nomeDe(por)]?.apelido || nomeDe(aguardandoNf(por));
const pinta = por => { const c = pessoas[nomeDe(por)]?.cor; return c ? `<b style="color:${c}">${esc(nomeVisto(por))}</b>` : esc(nomeVisto(por)) };
const nomeDe = nome => /\s/.test(nome) ? nome : nome.replace(/\./g, ' ');
// marcada atendida no sistema, mas a TOTVS ainda não atendeu (rotas/totvs.py continua conferindo)
const naoBate = t => t.por && t.totvs && t.totvs !== 'Atendida';
// cancelada ou recusada na TOTVS: não vai ser atendida, aparece mesmo entre várias transações (rotas/totvs.py IGNORAR)
const cancelada = t => ['Cancelada', 'Recusada'].includes(t.totvs);
// O que falta para a rota sair: cargas atendidas, transações atendidas (e batendo com a TOTVS), pedidos sem problema. RT e CX não são obrigatórios.
function faltas(r) {
  const f = [], nome = c => tipos.find(t => t.codigo === c)?.nome || c;
  for (const p of r.paradas) {
    if (!p.pes) continue;
    const de = ` de ${p.loja}`;
    for (const t of tipos) {
      const ds = p.docs[t.codigo] || [];
      if (t.eh_carga && !ds.length) f.push('carga' + de);
      for (const d of ds) {
        if (t.eh_carga && d.etapa !== 'atendida') f.push(`carga ${d.numero}${de}`);
        if (!t.eh_carga && !d.por) f.push(`${t.nome} ${d.numero}${de}${cancelada(d) ? ` (TOTVS: ${d.totvs})` : ''}`);
        if (naoBate(d)) f.push(`${t.nome} ${d.numero}${de} (TOTVS: ${d.totvs})`);
        if (d.pedidos) f.push(`pedidos da carga ${d.numero}${de}`);
      }
    }
  }
  return f;
}
// barra de progresso: desenha com o valor anterior e anda até o novo (o innerHTML recria tudo)
const progAntes = {};
function animarProg() {
  requestAnimationFrame(() => requestAnimationFrame(() => routes.querySelectorAll('[data-pr]').forEach(b => { b.style.width = b.dataset.p + '%'; progAntes[b.dataset.pr] = b.dataset.p })));
}
// verde rápido na célula que a própria pessoa acabou de marcar
const piscar = cel => cel && routes.querySelector(`td[data-cell="${CSS.escape(cel)}"]`)?.classList.add('okflash');
function rotaHTML(r, cols, q) {
  const rows = r.paradas.filter(p => {
    if (fatFiltro && fatFiltro !== 'all' && p.status !== fatFiltro) return false; if (fatFiltro === 'all' && !p.status) return false;
    return !q || [p.loja, p.pes, ...Object.values(p.docs).flat().map(d => d.numero)].join(' ').toLowerCase().includes(q);
  });
  if (!rows.length && (q || fatFiltro)) return '';
  const id = r.id, cancel = r.status === 'cancelada';
  const f = (k, l, val, extra = '') => `<div class="field" data-cell="${id}.r.${k}"><span class="k">${l}</span><span class="v ${extra}" contenteditable="plaintext-only" data-r="${id}" data-route="${k}">${esc(val)}</span></div>`;
  const falta = r.status === 'cancelada' ? null : faltas(r);
  const pronta = !falta ? '' : falta.length ? `<div class="faltas"><b>Falta:</b> ${falta.map(esc).join(' · ')}</div>` : '';
  const tags = (falta && !falta.length ? `<span class="tag pronta">Pronta para sair</span>` : '') + (r.fora_do_planejamento ? `<span class="tag extra">Fora do planejamento</span>` : '') + (r.remarcacoes ? `<span class="tag moved">Remarcada ${r.remarcacoes}x</span>` : '') + (cancel ? `<span class="tag cancel">Cancelada</span>` : '');
  const mv = movendo === id ? `<span class="move"><label for="mv-${id}">Mover para</label><input type="date" id="mv-${id}" value="${addDays(dia, 1)}"><button class="btn sm primary" data-act="domove" data-r="${id}">Mover</button><button class="btn sm" data-act="nomove" data-r="${id}">Voltar</button></span>`
    : `<button class="btn sm" data-act="move" data-r="${id}" ${cancel ? 'disabled' : ''}>Mover para outro dia</button>`;
  const lojas = r.paradas.filter(p => p.pes), feitas = lojas.filter(p => p.status === 'atendida').length;
  const prog = cancel || !lojas.length ? '' : `<span class="prog${feitas === lojas.length ? ' cheio' : ''}" title="Lojas com tudo atendido"><span>${feitas} de ${lojas.length} atendidas</span><i><b data-pr="${id}" data-p="${100 * feitas / lojas.length}" style="width:${progAntes[id] ?? 100 * feitas / lojas.length}%"></b></i></span>`;
  return `<article class="route ${cancel ? 'cancelada' : ''}"><div class="rhead">
    <div class="rname" data-cell="${id}.r.nome"><small>ROTA</small><input class="rnamein" list="dl-rotas" data-r="${id}" value="${esc(r.nome)}" aria-label="Nome da rota">${tags}${prog}</div>
    ${f('saida', 'Saída', r.saida, 'mono')}
    <div class="field" data-cell="${id}.r.lacre"><span class="k">Lacre</span><span class="v seal mono" contenteditable="plaintext-only" data-r="${id}" data-route="lacre">${esc(r.lacre)}</span></div>
    ${f('motorista', 'Motorista', r.motorista)}
    <div class="field" data-cell="${id}.r.veiculo"><span class="k">Veículo</span><span class="plate"><span contenteditable="plaintext-only" data-r="${id}" data-route="veiculo">${esc(r.veiculo)}</span></span>${r.tipo_veiculo ? `<span class="tipo">${r.tipo_veiculo}</span>` : r.veiculo ? `<span class="tipo sem" title="Cadastre a placa em Cadastro → Veículos para calcular o frete">placa sem cadastro</span>` : ''}</div>
    <div class="field" data-cell="${id}.r.ajudantes"><span class="k">Ajudante(s) <button class="maisaj" data-act="maisaj" data-r="${id}" title="Adicionar outro ajudante" aria-label="Adicionar outro ajudante">+</button></span><span class="v mono" contenteditable="plaintext-only" data-r="${id}" data-route="ajudantes" title="Vários ajudantes: separe por vírgula">${esc(r.ajudantes)}</span></div>
    ${f('rastreador', 'Rastreador', r.rastreador ? 'R:' + r.rastreador : '', 'mono')}
  </div>${pronta}
  <div class="ractions">${mv}
    <button class="btn sm primary" data-act="capa" data-r="${id}" ${cancel ? 'disabled' : ''}>Imprimir capa</button>
    ${document.body.dataset.dono ? `<button class="btn sm" data-act="entrega" data-r="${id}" ${cancel ? 'disabled' : ''} title="Relatório de entrega de mercadoria (553) de cada 434 e 3386 da rota, com os dados da TOTVS">Folhas 553</button>` : ''}
    <button class="btn sm" data-act="inv" data-r="${id}" ${cancel ? 'disabled' : ''} title="Vira a ordem das lojas; a FÁBRICA fica no começo e no fim">Inverter ordem</button>
    <button class="btn sm" data-act="dup" data-r="${id}" title="Cria uma cópia no mesmo dia para dividir a rota">Duplicar rota</button>
    ${cancel ? `<button class="btn sm" data-act="reativar" data-r="${id}">Reativar rota</button>` : `<button class="btn sm" data-act="cancel" data-r="${id}">Cancelar rota</button>`}
    <button class="btn sm" data-act="excluir" data-r="${id}" title="Apaga a rota de vez, com as lojas e o histórico. Para guardar no histórico, use Cancelar">Excluir rota</button>
    ${r.historico.length ? `<details class="hist" data-hist="${id}"><summary>Histórico (${r.historico.length})</summary><ul>${r.historico.map(h => `<li>${esc(h)}</li>`).join('')}</ul></details>` : ''}
  </div>
  <div class="tbl" data-tbl="${id}"><table><thead><tr>${cols.map(c => `<th title="${c.t || ''}"${c.k === 'loja' ? ' class="fixa"' : ''}>${esc(c.l)}</th>`).join('')}</tr></thead>
  <tbody>${rows.map(p => `<tr class="${p.pes ? '' : 'base'}">${cols.map(c => `<td data-cell="${id}.${p.id}.${c.k}" data-label="${esc(c.l)}"${c.k === 'loja' ? ' class="fixa"' : ''}>${cell(c, p, r)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
  <div class="rfoot"><button class="btn sm" data-act="add" data-r="${id}">+ Adicionar parada</button></div></article>`;
}
const EDIT = '[contenteditable],input.lojain,input.rnamein';
const posicao = () => ({ y: scrollY, tbl: Object.fromEntries([...routes.querySelectorAll('.tbl[data-tbl]')].map(t => [t.dataset.tbl, t.scrollLeft])) });
function voltarPosicao(p) { routes.querySelectorAll('.tbl[data-tbl]').forEach(t => { t.scrollLeft = p.tbl[t.dataset.tbl] || 0 }) }
function renderRoutes() {
  // Quem está editando não perde o que digitou: a célula em foco é recriada com o texto dela.
  const a = document.activeElement, holder = routes.contains(a) && a.matches(EDIT) ? a.closest('[data-cell]') : null;
  const keep = holder && { k: holder.dataset.cell, val: a.tagName === 'INPUT' ? a.value : a.textContent, all: caret(a).all };
  const abertos = [...routes.querySelectorAll('details[open]')].map(d => d.dataset.hist), pos = posicao();
  const cols = colunas().filter(c => !visao.colunas_ocultas.includes(c.k)), qq = visao.filtro.trim().toLowerCase();
  redesenhando = true;
  const ordem = [...rotas].sort((x, y) => (x.status === 'cancelada') - (y.status === 'cancelada'));  // canceladas no fim
  routes.innerHTML = ordem.map(r => rotaHTML(r, cols, qq)).join('') || `<div class="empty">Nenhuma rota ${qq || fatFiltro ? 'com esse filtro' : 'neste dia'}. Use "Adicionar rota" abaixo.</div>`;
  abertos.forEach(id => routes.querySelector(`details[data-hist="${id}"]`)?.setAttribute('open', ''));
  voltarPosicao(pos);  // alteração de outra tela não leva a tabela de volta à primeira coluna
  if (keep) {
    const e = editIn(routes.querySelector(`[data-cell="${CSS.escape(keep.k)}"]`));
    if (e) { if (e.tagName === 'INPUT') e.value = keep.val; else e.textContent = keep.val; e.focus(); selecionar(e, keep.all) }
  }
  redesenhando = false;
  animarProg();
}
function render() { renderWeek(); renderFat(); renderCols(); renderRoutes() }

let tt;
function toast(t) {
  document.querySelector('.toast')?.remove(); const el = document.createElement('div');
  el.className = 'toast'; el.setAttribute('role', 'status'); el.textContent = t; document.body.append(el);
  clearTimeout(tt); tt = setTimeout(() => el.remove(), 4200);
}

// Problema que chega de outra tela ou da TOTVS: som e aviso que fica até fechar, mesmo com a rota fora da tela.
const ALERTA = /conferiu os pedidos da carga .*: (?!todos certos$)|\(marcada atendida no sistema\)/;
function alertar(texto) {
  try {
    const a = new AudioContext(), o = a.createOscillator(), g = a.createGain();
    o.frequency.value = 880; g.gain.setValueAtTime(0.2, a.currentTime); g.gain.exponentialRampToValueAtTime(0.001, a.currentTime + 0.6);
    o.connect(g).connect(a.destination); o.start(); o.stop(a.currentTime + 0.6);
  } catch { }  // sem som (navegador bloqueou): o aviso aparece igual
  let box = document.querySelector('.alertas');
  if (!box) { box = document.createElement('div'); box.className = 'alertas'; box.setAttribute('role', 'alert'); document.body.append(box) }
  const el = document.createElement('div'); el.className = 'alerta';
  el.innerHTML = `<span>${esc(texto)}</span><button aria-label="Fechar aviso">x</button>`;
  el.querySelector('button').onclick = () => el.remove();
  box.append(el);
}

// ---------- navegação de planilha ----------
const editIn = el => el?.matches?.(EDIT) ? el : el?.querySelector(EDIT);
function selecionar(e, tudo = true) {
  if (e.tagName === 'INPUT') { tudo ? e.select() : e.setSelectionRange(e.value.length, e.value.length); return }
  const r = document.createRange(); r.selectNodeContents(e); if (!tudo) r.collapse(false);
  const s = getSelection(); s.removeAllRanges(); s.addRange(r);
}
function editarNums(e, abrir) {  // várias transações: troca a linha de números clicáveis pela edição (e volta)
  const w = e.closest('.numsed'); if (w) { w.hidden = !abrir; w.previousElementSibling.hidden = abrir }
}
function focusCell(k) { const e = editIn(routes.querySelector(`td[data-cell="${CSS.escape(k)}"]`)); if (e) { editarNums(e, true); e.focus(); selecionar(e) } }
function caret(el) {
  if (el.tagName === 'INPUT') { const a = el.selectionStart, b = el.selectionEnd, n = el.value.length; return { start: a === 0 && b === 0, end: a === n, all: a === 0 && b === n } }
  const s = getSelection(); if (!s.rangeCount || !el.contains(s.anchorNode)) return { start: true, end: true, all: true };
  const r = s.getRangeAt(0), n = el.textContent.length, pre = document.createRange();
  pre.selectNodeContents(el); pre.setEnd(r.startContainer, r.startOffset);
  const a = pre.toString().length, b = a + r.toString().length; return { start: a === 0 && b === 0, end: a === n, all: a === 0 && b === n };
}
routes.addEventListener('keydown', e => {
  const el = e.target, td = el.closest?.('td[data-cell]');
  if (!td || !el.matches(EDIT)) { if (e.key === 'Enter' && el.matches(EDIT)) { e.preventDefault(); el.blur() } else if (e.key === 'Escape' && el.matches(EDIT)) el.blur(); return }
  const rows = [...routes.querySelectorAll('tbody tr')], tr = td.parentElement, ri = rows.indexOf(tr), ci = [...tr.children].indexOf(td);
  const flat = rows.flatMap(r => [...r.children].filter(editIn)), fi = flat.indexOf(td), c = caret(el), lista = el.classList.contains('lojain');
  const vert = i => { const t = rows[i]?.children[ci]; return editIn(t) ? t : null };
  let tgt;
  if (e.key === 'Tab') { tgt = flat[fi + (e.shiftKey ? -1 : 1)]; if (!tgt) return }  // fim da tabela: o Tab segue para fora
  else if (e.key === 'Enter') tgt = vert(ri + (e.shiftKey ? -1 : 1));
  else if (e.key === 'ArrowDown' && !lista) tgt = vert(ri + 1);  // na loja, ↑↓ ficam para a lista de sugestões
  else if (e.key === 'ArrowUp' && !lista) tgt = vert(ri - 1);
  else if (e.key === 'ArrowLeft' && (c.start || c.all)) tgt = flat[fi - 1];
  else if (e.key === 'ArrowRight' && (c.end || c.all)) tgt = flat[fi + 1];
  else if (e.key === 'Escape') { el.blur(); return }
  else return;
  e.preventDefault(); if (!tgt) { if (e.key === 'Enter') el.blur(); return }
  const k = tgt.dataset.cell; el.blur(); focusCell(k);
});

// ---------- edição ----------
routes.addEventListener('focusin', e => {
  const m = e.target.closest('[data-money]');
  if (m && !redesenhando) { const v = parada(rota(m.dataset.r), m.dataset.p)[m.dataset.f]; m.textContent = v ? String(v).replace('.', ',') : ''; selecionar(m) }
});
routes.addEventListener('focusout', e => {
  const el = e.target; if (!el.isContentEditable || redesenhando) return;
  const r = rota(el.dataset.r); if (!r) return; const val = el.textContent.trim();
  if (el.dataset.route) {
    const k = el.dataset.route, atual = k === 'rastreador' && r[k] ? 'R:' + r[k] : r[k];
    if (val !== atual) enviar(`/api/rota/${r.id}/campo/`, { campo: k, valor: val }, k === 'veiculo' && val ? 'Placa salva. Se tiver cadastro, o frete das lojas é recalculado.' : undefined);
    return;
  }
  const p = parada(r, el.dataset.p), f = el.dataset.f;
  if (f.startsWith('doc:')) {
    const codigo = f.slice(4);
    const atual = (p.docs[codigo] || []).map(d => d.numero).join(' ');
    if (val.split(/[\s,;/+]+/).filter(Boolean).join(' ') !== atual) enviar(`/api/parada/${p.id}/documento/`, { tipo: codigo, numero: val });
    else { el.textContent = atual.split(' ').join(' / '); editarNums(el, false) }  // sem mudança: volta aos números clicáveis
  } else if (el.dataset.money) {
    if (val === (p[f] ? String(p[f]).replace('.', ',') : '')) el.textContent = money(p[f]);
    else enviar(`/api/parada/${p.id}/campo/`, { campo: f, valor: val });
  } else if (val !== p[f]) enviar(`/api/parada/${p.id}/campo/`, { campo: f, valor: val });
  else editarNums(el, false);  // retirada sem mudança: volta ao volume clicável
});
// Loja e nome da rota: pesquisa na lista; a loja do cadastro já traz PES, EMP, horário e frete.
routes.addEventListener('change', e => {
  const el = e.target, r = rota(el.dataset.r); if (!r) return; const v = el.value.trim().toUpperCase();
  if (el.classList.contains('lojain')) {
    const p = parada(r, el.dataset.p); if (p.loja === v) return; const l = cad.lojas[v];
    enviar(`/api/parada/${p.id}/campo/`, { campo: 'loja', valor: v }, l ? `${v} · PES ${l.pes || '—'} · EMP ${l.emp || '—'} preenchidos` : `Loja ${v || 'vazia'} salva (fora do cadastro)`);
  } else if (el.classList.contains('rnamein')) {
    if (!v || r.nome === v) { el.value = r.nome; return }
    enviar(`/api/rota/${r.id}/campo/`, { campo: 'nome', valor: v }, `Rota renomeada para ${v}`);
  }
});
routes.addEventListener('click', e => {
  const a = e.target.closest('[data-act]'); if (!a) return;
  const act = a.dataset.act, r = rota(a.dataset.r), p = r && a.dataset.p ? parada(r, a.dataset.p) : null;
  // Ações destrutivas pedem um segundo clique.
  if ((act === 'del' || act === 'cancel' || act === 'excluir' || act === 'desatender' || act === 'desretirada' || act === 'voltar') && !a.dataset.armed) {
    const t = a.textContent; a.dataset.armed = 1; a.textContent = act === 'desatender' || act === 'desretirada' ? 'Desfazer?' : 'Confirmar?';
    setTimeout(() => { if (a.isConnected) { delete a.dataset.armed; a.textContent = t } }, 3000); return;
  }
  if (act === 'up' || act === 'down') enviar(`/api/parada/${p.id}/ordem/`, { direcao: act === 'up' ? -1 : 1 });
  else if (act === 'del') enviar(`/api/parada/${p.id}/remover/`, {}, `Parada ${p.loja || ''} removida`);
  else if (act === 'add') enviar(`/api/rota/${r.id}/parada/`, {}, 'Parada adicionada no fim da rota');
  else if (act === 'avancar' || act === 'voltar') enviar(`/api/documento/${a.dataset.doc}/etapa/`, { acao: act }, act === 'voltar' ? 'Carga voltou uma etapa' : undefined);
  else if (act === 'atender') { const cel = a.closest('td')?.dataset.cell; toast('Conferindo na TOTVS...'); enviar(`/api/documento/${a.dataset.doc}/atender/`, { sim: true }, 'Atendimento confirmado', true).then(() => piscar(cel)) }
  else if (act === 'desatender') enviar(`/api/documento/${a.dataset.doc}/atender/`, { sim: false }, 'Atendimento desfeito');
  else if (act === 'retirada' || act === 'desretirada') { const cel = act === 'retirada' && a.closest('td')?.dataset.cell; enviar(`/api/parada/${p.id}/retirada/`, { campo: a.dataset.campo, sim: act === 'retirada' }, act === 'retirada' ? `Retirada confirmada por ${EU}` : 'Confirmação da retirada desfeita').then(() => piscar(cel)) }
  else if (act === 'pedidos') abrirPedidos(p, Object.values(p.docs).flat().find(d => d.id == a.dataset.doc));
  else if (act === 'capa') abrirCapa(r);
  else if (act === 'entrega') abrirEntrega(r);
  else if (act === 'editnums') {  // abre a edição já com " / " no fim, pronta para o próximo número
    const el = a.closest('td').querySelector('[contenteditable]');
    editarNums(el, true); if (el.dataset.f.startsWith('doc:')) el.textContent = el.textContent.trim() + ' / '; el.focus(); selecionar(el, false);
  }
  else if (act === 'maisaj') {  // prepara o próximo nome no fim do campo
    const el = a.closest('.field').querySelector('[data-route=ajudantes]');
    if (el.textContent.trim()) el.textContent = el.textContent.trim() + ' / ';
    el.focus(); selecionar(el, false);
  }
  else if (act === 'inv') enviar(`/api/rota/${r.id}/inverter/`, {}, `Ordem das lojas de ${r.nome} invertida`);
  else if (act === 'dup') api(`/api/rota/${r.id}/duplicar/`, {}).then(j => { trocarRota(j.rota); toast(`Criada ${j.rota.nome}: ajuste as lojas de cada uma`) }).catch(e => toast(e.message));
  else if (act === 'move') { movendo = r.id; renderRoutes() }
  else if (act === 'nomove') { movendo = null; renderRoutes() }
  else if (act === 'domove') {
    const alvo = $('mv-' + r.id).value; if (!alvo || alvo === dia) { toast('Escolha um dia diferente do atual.'); return }
    movendo = null; enviar(`/api/rota/${r.id}/mover/`, { data: alvo }, `Rota ${r.nome} movida para ${DOW[parse(alvo).getDay()]} ${fmt(alvo)}`);
  }
  else if (act === 'cancel') enviar(`/api/rota/${r.id}/status/`, { status: 'cancelada' }, `Rota ${r.nome} cancelada. Ela continua aqui no histórico.`);
  else if (act === 'excluir') api(`/api/rota/${r.id}/excluir/`, {}).then(() => { rotas.splice(rotas.indexOf(r), 1); carregarSemana(); renderFat(); renderRoutes(); toast(`Rota ${r.nome} excluída`) }).catch(e => toast(e.message));
  else if (act === 'reativar') enviar(`/api/rota/${r.id}/status/`, { status: 'ativa' }, `Rota ${r.nome} reativada`);
});

// ---------- barra, filtros e colunas ----------
const irPara = () => { try { sessionStorage.setItem('dia', dia) } catch { } movendo = null; fatFiltro = null; syncAdd(); renderWeek(); carregarSemana(); carregarDia() };
week.onclick = e => {
  if (e.target.matches('.cal')) { try { e.target.showPicker() } catch { } return }  // abre o mês inteiro ao clicar em qualquer ponto
  const b = e.target.closest('button'); if (!b) return;
  if (b.matches('.rsem')) { abrirSemana(); return }  // documento.js
  if (b.dataset.w) { semana = addDays(semana, +b.dataset.w); dia = semana } else dia = b.dataset.k;
  irPara();
};
week.onchange = e => {
  if (!e.target.matches('.cal') || !e.target.value) return;
  dia = e.target.value; semana = segunda(dia); irPara();
};
fat.onclick = e => { const b = e.target.closest('.kpi'); if (b) { fatFiltro = fatFiltro === b.dataset.f ? null : b.dataset.f; renderFat(); renderRoutes() } };
colbtn.onclick = () => { colpop.hidden = !colpop.hidden; colbtn.setAttribute('aria-expanded', !colpop.hidden) };
document.addEventListener('click', e => { if (!e.target.closest('.colmenu')) { colpop.hidden = true; colbtn.setAttribute('aria-expanded', 'false') } });
colpop.onchange = e => {
  const k = e.target.dataset.k, h = visao.colunas_ocultas;
  visao.colunas_ocultas = e.target.checked ? h.filter(x => x !== k) : [...h, k]; salvarVisao(); renderCols(); renderRoutes();
};
q.oninput = () => { visao.filtro = q.value; salvarVisao(); renderRoutes() };

// ---------- adicionar rota ----------
const catRota = v => Object.keys(cad.rotas).find(k => k === v.trim().toUpperCase());
function addPreview() {
  const k = catRota(addq.value), c = cad.rotas[k];
  addlojas.innerHTML = k ? `<p class="hint">Saída padrão <b>${esc(c.saida)}</b>. Desmarque as lojas que não vão nesta rota.${c.variacoes.length ? ' Variações: ' + c.variacoes.map(esc).join(' · ') : ''}</p>`
    + c.lojas.map((n, i) => { const l = cad.lojas[n] || {}; return `<label for="al-${i}"><input type="checkbox" id="al-${i}" data-loja="${esc(n)}" checked>${esc(n)} <small>PES ${esc(l.pes) || '—'} · EMP ${esc(l.emp) || '—'}${l.horario ? ' · ' + esc(l.horario) : ''}</small></label>` }).join('')
    : addq.value.trim() ? `<p class="hint">"${esc(addq.value.trim().toUpperCase())}" não está no cadastro: a rota será criada só com a FÁBRICA, e você adiciona as paradas.</p>` : '';
}
function syncAdd() { addextra.checked = dia <= HOJE }  // rota criada no próprio dia (ou depois) é fora do planejamento
addq.oninput = addPreview;
addgo.onclick = async () => {
  const nome = addq.value.trim().toUpperCase(); if (!nome) { toast('Pesquise ou digite o nome da rota.'); addq.focus(); return }
  const lojas = [...addlojas.querySelectorAll('input[data-loja]:checked')].map(i => i.dataset.loja);
  try {
    const { rota: r } = await api('/api/rota/', { data: dia, nome, lojas, fora_do_planejamento: addextra.checked });
    addq.value = ''; addPreview(); syncAdd(); trocarRota(r); toast(`Rota ${r.nome} criada com ${lojas.length} loja(s)`);
  } catch (e) { toast(e.message) }
};

// ---------- mudanças dos outros (a cada 1 s) ----------
async function buscarMudancas() {
  if (document.hidden) return;
  let j; try { j = await api(`/api/mudancas/?desde=${ultimo}`) } catch (e) { return }  // servidor reiniciando: tenta de novo daqui a 1 s
  ultimo = Math.max(ultimo, j.ultimo);
  versao ??= j.versao;
  if (j.versao !== versao) { recarregarQuandoLivre(); return }
  const outros = j.eventos.filter(e => e.usuario !== EU); if (!outros.length) return;
  outros.filter(e => ALERTA.test(e.texto)).forEach(e => alertar(e.texto));
  carregarSemana();
  const ids = new Set(rotas.map(r => r.id)), daqui = outros.filter(e => e.data === dia || ids.has(e.rota)); if (!daqui.length) return;
  await carregarDia();
  for (const e of daqui) {
    if (!e.parada || !e.campo) continue;
    const k = tipos.some(t => t.codigo === e.campo) ? 'doc:' + e.campo : e.campo;
    routes.querySelector(`td[data-cell="${CSS.escape(`${e.rota}.${e.parada}.${k}`)}"]`)?.classList.add('flash');
  }
  toast(daqui[daqui.length - 1].texto + (daqui.length > 1 ? ` (+${daqui.length - 1} alterações)` : ''));
}
let buscando = false;  // resposta lenta: não começa outra busca (repetiria os avisos)
setInterval(() => { if (!buscando) { buscando = true; buscarMudancas().finally(() => buscando = false) } }, 1000);

// Atualização no servidor: recarrega a tela, mas espera quem está digitando sair da célula.
let versao = null, recarregando = false;
function recarregarQuandoLivre() {
  if (recarregando) return;
  if (document.activeElement?.matches?.(EDIT)) { document.activeElement.addEventListener('blur', () => setTimeout(recarregarQuandoLivre, 1500), { once: true }); return }
  recarregando = true; toast('Sistema atualizado. Recarregando…');
  setTimeout(() => { try { sessionStorage.setItem('posicao', JSON.stringify(posicao())) } catch { } location.reload() }, 1500);
}

$('cfgbtn').onclick = () => $('cfg').showModal();
$('cfgfechar').onclick = () => $('cfg').close();
$('apelido').onchange = e => { visao.apelido = e.target.value.trim(); salvarVisao(); setTimeout(() => location.reload(), 600) };
$('minhacor').onchange = e => { visao.cor = e.target.value; salvarVisao(); setTimeout(carregarDia, 600) };

// ---------- início ----------
(async () => {
  syncAdd();
  try {
    [cad, visao] = await Promise.all([api('/api/cadastro/'), api('/api/visao/')]);
    listasCadastro(); $('minhacor').value = visao.cor || '#a3a3a3'; $('apelido').value = visao.apelido || '';
  } catch (e) { toast(e.message) }
  await Promise.all([carregarSemana(), carregarDia()]);
  try { const p = JSON.parse(sessionStorage.getItem('posicao')); if (p) { sessionStorage.removeItem('posicao'); voltarPosicao(p); scrollTo(0, p.y) } } catch { }  // volta onde estava antes da atualização
})();

// App no celular: o service worker só funciona com https (ou localhost); pela rede interna em http, segue como site.
if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => {});
