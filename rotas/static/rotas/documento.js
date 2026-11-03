'use strict';
// Rota do dia (e-mail/WhatsApp) e capa da frente (A4 com o motorista): o mesmo diálogo, com cópia, PNG e PDF.
const dlg = $('dlg'), dtitle = $('dtitle'), dmsg = $('dmsg'), paper = $('paper');
let dlgNome = '';

function abrir(titulo, html, msg, nome) { dtitle.textContent = titulo; paper.innerHTML = html; dmsg.textContent = msg; dlgNome = nome; dlg.showModal() }

const simNao = v => v === 'SIM' ? '<span class="sim">SIM</span>' : v === 'NÃO' ? '<span class="nao">NÃO</span>' : esc(v);
function capaHTML(c) {
  const linhas = c.lojas.map(l => `<tr><td class="atdn">${l.atd}º</td><td class="b">${esc(l.nome)}</td><td class="c">${esc(l.horario)}</td><td class="c">${esc(l.ramal)}</td><td class="c tel">${esc(l.oi)}</td><td class="c">${l.rt || ''}</td><td class="c">${l.cx || ''}</td><td class="c tt">${l.total || ''}</td></tr>`).join('');
  const regras = c.regras.map(g => `<tr><td class="b">${esc(g.loja)}</td><td class="c">${esc(g.carrinho_4)}</td><td class="c">${esc(g.carrinho_2)}</td><td class="c">${simNao(g.sem_carrinho_apos)}</td><td class="c">${esc(g.horario_docas)}</td><td class="c">${simNao(g.autorizacao_previa)}</td></tr>`
    + (g.observacao ? `<tr><td class="obs" colspan="6">${esc(g.loja)}: ${esc(g.observacao)}</td></tr>` : '')).join('');
  return `<div class="sheet">
    <table class="cp"><tr><th class="rn" colspan="3">${esc(c.rota)}</th><th>RAMAL</th><th>OI DA LOJA</th><th>RETIRADA DE MERCADORIA</th><th>CAIXA VAZIA</th><th>TOTAL</th></tr>
      ${linhas || '<tr><td colspan="8" class="c">Nenhuma loja nesta rota.</td></tr>'}
      <tr class="tot"><td colspan="7">TOTAL DE RETIRADA NA ROTA:</td><td>${c.total}</td></tr></table>
    ${regras ? `<table class="cp reg"><tr><th class="rn">${esc(c.rota)}</th><th>HORÁRIO DE MERCADORIA C/ CARRINHO 4 RODAS</th><th>HORÁRIO DE MERCADORIA C/ CARRINHO 2 RODAS</th><th>ENTREGA SEM CARRINHO APÓS HORÁRIO</th><th>HORÁRIO DAS DOCAS</th><th>AUTORIZAÇÃO PRÉVIA</th></tr>${regras}</table>` : ''}
    <div class="info"><div><span>DATA</span><b>${c.data}</b></div><div><span>CARRO</span><b>${esc(c.veiculo) || '&nbsp;'}</b></div>
      <div><span>MOTORISTA</span><b>${esc(c.motorista) || '&nbsp;'}</b></div><div><span>AJUDANTES</span><b>${esc(c.ajudantes) || '&nbsp;'}</b></div>
      <div class="lacre"><span>Nº DO LACRE</span><b>${c.lacre ? esc(c.lacre) : '<i class="linha"></i>'}</b></div></div></div>`;
}
async function abrirCapa(r) {
  try {
    const c = await api(`/api/rota/${r.id}/capa/`);
    abrir('Capa da frente', capaHTML(c), `Capa da rota ${c.rota} de ${c.data}. Vai na frente da carga, com o motorista.`, `Capa da frente — ${c.rota} — ${c.data.replaceAll('/', '_')}`);
  } catch (e) { toast(e.message) }
}

// Folha 553 (Relatório de entrega de mercadoria): uma por transação, no layout da 553 da TOTVS; contagem e pedido virtual em branco
// (a API da TOTVS não liga a transação a eles).
const assina = (a, b) => `<div class="ass"><span>${a}</span><i></i></div><div class="ass"><span>${b}</span><i></i></div>`;
function folhaHTML(f, rota, pagina) {
  const dt = f.data ? f.data.split('-').reverse().join('/') : '', agora = new Date(), q = f.quantidade ?? '', cx = f.caixa ?? '';
  const emitido = agora.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit' }) + ' ' + agora.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  return `<div class="sheet f553"><header class="fh"><img src="/static/rotas/logo.svg" alt="Rotas Expedição">
      <div><h2>553_RELATORIO_DE_ENTREGA_DE_MERCADORIA</h2><p>Filtro: Data = [&gt;= ${dt}] /<br>Nr. Trans = [${esc(f.numero)}] · ${esc(rota)}</p></div></header>
    ${f.erro ? `<p class="ferro">${esc(f.operacao)} ${esc(f.numero)}: ${esc(f.erro)}. Confira o número na rota.</p>` : ''}
    <div class="fu">USUÁRIO: ${esc(f.usuario || '')} - NOTA FISCAL: ${esc(f.nf ?? '')}</div>
    <div class="fl">COD LOJA: ${f.pessoa} - LOJA: ${esc(f.loja)}</div>
    <div class="fo">OPERAÇÃO: ${esc(f.operacao)}</div>
    <table class="ft"><tr><th>DATA</th><th>CONTAGEM</th><th>PED.VIRTUAL</th><th class="n">QUANTIDADE</th><th>CAIXA</th><th>Ds. Campo</th></tr>
      <tr><td>${dt}</td><td></td><td></td><td class="n">${q}</td><td>${cx}</td><td></td></tr>
      <tr class="tot"><td>TOTAL -&gt;</td><td></td><td></td><td class="n">${q}</td><td>${cx}</td><td></td></tr></table>
    <div class="assinaturas">${assina('ASS EXPEDICAO :', 'OCORRENCIA :')}${assina('ASS LOJA 1 :', '')}${assina('ASS LOJA 2 :', '')}${assina('CARIMBO :', '')}
      <div class="ass"><span>DATA : _______ / ________ / ${agora.getFullYear()}</span></div><div class="ass"><i></i></div></div>
    <footer>Rel:0553 / Usu:${esc(f.usuario || '')} / Dat:${emitido} / Página: ${pagina}</footer></div>`;
}
async function abrirEntrega(r) {
  toast('Buscando na TOTVS...');
  try {
    const c = await api(`/api/rota/${r.id}/entrega/`);
    if (!c.folhas.length) return toast('Nenhuma 434 ou 3386 com número nesta rota.');
    const erros = c.folhas.filter(f => f.erro).length;
    abrir('Folhas 553', c.folhas.map((f, i) => folhaHTML(f, c.rota, i + 1)).join(''),
      `${c.folhas.length} folha(s) da rota ${c.rota}, uma por página no PDF.` + (erros ? ` ${erros} sem dados da TOTVS (aviso em vermelho na folha).` : ''),
      `553 — ${c.rota} — ${c.data.replaceAll('/', '_')}`);
  } catch (e) { toast(e.message) }
}

// Rotas canceladas não entram.
function rotaDoDiaHTML() {
  return `<div class="rdia"><h3>Rota do dia <small>${DOW[parse(dia).getDay()]} · ${fmt(dia)}/${dia.slice(0, 4)}</small></h3>` + rotasHTML(rotas) + '</div>';
}
// Rota da semana: o mesmo quadro, um bloco por dia que tem rota (segunda a domingo).
async function abrirSemana() {
  try {
    const comRota = dias.filter(d => d.ativas).map(d => d.data);
    const porDia = await Promise.all(comRota.map(k => api(`/api/dia/${k}/`)));
    const fim = addDays(semana, 6);
    const html = `<div class="rdia"><h3>Rota da semana <small>${fmt(semana)} a ${fmt(fim)}/${fim.slice(0, 4)}</small></h3>`
      + (comRota.length ? comRota.map((k, i) => `<h4>${DOW[parse(k).getDay()]} · ${fmt(k)}</h4>` + rotasHTML(porDia[i].rotas)).join('') : '<p>Nenhuma rota ativa nesta semana.</p>') + '</div>';
    abrir('Rota da semana', html, 'Versão para e-mail e grupos de WhatsApp. Rotas canceladas não entram.', `ROTA SEMANA ${fmt(semana).replace('/', '.')} a ${fmt(fim).replace('/', '.')}`);
  } catch (e) { toast(e.message) }
}
function rotasHTML(todas) {
  const rs = todas.filter(r => r.status === 'ativa');
  // como a planilha: lojas à esquerda; à direita motorista, veículo (com o rastreador embaixo) e ajudantes, um por linha
  const linhas = v => (v || '').split(/\s*[,/]\s*/).filter(Boolean).map(esc).join('<br>') || '—';
  return rs.length ? rs.map(r => `<div class="pr"><div class="ph"><b>${esc(r.nome)}</b>
    <span>Saída ${esc(r.saida || '—')}${r.lacre ? ' · Lacre ' + esc(r.lacre) : ''}</span></div>
    <div class="pc"><table><thead><tr><th>ATD</th><th>PES</th><th>Lojas</th><th>EMP</th><th>Horário da loja</th></tr></thead><tbody>${r.paradas.map(p => `<tr><td>${p.ordem}º</td><td>${esc(p.pes)}</td><td><b>${esc(p.loja)}</b></td><td>${esc(p.emp)}</td><td>${esc(p.horario)}</td></tr>`).join('')}</tbody></table>
    <div class="pe"><div><span>MOTORISTA</span><b>${esc(r.motorista) || '—'}</b></div>
      <div><span>VEÍCULO</span><b>${esc(r.veiculo) || '—'}</b>${r.rastreador ? `<b>R:${esc(r.rastreador)}</b>` : ''}</div>
      <div><span>AJUDANTES</span><b>${linhas(r.ajudantes)}</b></div></div></div></div>`).join('') : '<p>Nenhuma rota ativa neste dia.</p>';
}
$('share').onclick = () => abrir('Rota do dia', rotaDoDiaHTML(), 'Versão para e-mail e grupos de WhatsApp. Rotas canceladas não entram.', `ROTA ${fmt(dia).replace('/', '.')}`);
$('close').onclick = () => dlg.close();

// A imagem sai sempre com fundo branco, em qualquer tema. 3x: letra nítida mesmo quando o WhatsApp comprime.
const ESCALA = 3, imagem = () => html2canvas(paper, { scale: ESCALA, backgroundColor: '#ffffff' });
function baixar(blob, nome) { const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = nome; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000) }

async function copiar() {
  dmsg.textContent = 'Gerando imagem…';
  const cv = await imagem(), blob = await new Promise(r => cv.toBlob(r, 'image/png'));
  try {
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
    dmsg.textContent = 'Imagem copiada. Cole (Ctrl+V) no WhatsApp Web ou no e-mail.';
  } catch (e) {
    // fora de https (as outras máquinas, pela rede) o navegador não deixa o site copiar; o botão direito do navegador deixa
    paper.innerHTML = `<img src="${URL.createObjectURL(blob)}" style="width:${cv.width / ESCALA}px;max-width:100%;margin-inline:auto" alt="Imagem pronta para copiar">`;
    dmsg.textContent = 'Imagem pronta: clique com o botão direito nela e escolha "Copiar imagem". Depois cole (Ctrl+V) no WhatsApp Web ou no e-mail.';
  }
}
$('copy').onclick = copiar;
$('png').onclick = async () => {
  dmsg.textContent = 'Gerando PNG…';
  const cv = await imagem(); cv.toBlob(b => { baixar(b, dlgNome + '.png'); dmsg.textContent = `Baixado: ${dlgNome}.png` }, 'image/png');
};
$('pdf').onclick = async () => {
  dmsg.textContent = 'Gerando PDF…';
  const pdf = new window.jspdf.jsPDF({ unit: 'mm', format: 'a4' }), rdia = paper.querySelector('.rdia'), f553 = paper.querySelectorAll('.f553');
  if (f553.length) {  // folhas 553: uma por página
    for (const [i, el] of [...f553].entries()) {
      const cv = await html2canvas(el, { scale: ESCALA, backgroundColor: '#ffffff' });
      if (i) pdf.addPage();
      pdf.addImage(cv.toDataURL('image/png'), 'PNG', 0, 0, 210, Math.min(297, cv.height * 210 / cv.width));
    }
  } else if (!rdia) {  // capa (folha A4 pronta): conteúdo maior que uma folha continua na próxima
    const cv = await imagem(), w = 210, h = cv.height * w / cv.width, img = cv.toDataURL('image/png');
    for (let y = 0; y < h; y += 297) { if (y) pdf.addPage(); pdf.addImage(img, 'PNG', 0, -y, w, h) }
  } else {  // rota do dia/semana: cada rota inteira na folha; não cabe no que sobra, vai para a próxima
    const M = 12, W = 210 - 2 * M, H = 297 - 2 * M, blocos = [...rdia.children], cvs = [];
    const L = W * 0.75;  // rotas em 75% da largura (letra perto de 9 pt): cabem mais rotas por folha
    for (const el of blocos) cvs.push(await html2canvas(el, { scale: ESCALA, backgroundColor: '#ffffff' }));
    const alt = cv => cv.height * L / cv.width, titulo = i => /^H[34]$/.test(blocos[i]?.tagName);
    // espaço depois do bloco, em mm: título → rota 4; rota → rota 7; rota → outro dia 12
    const vao = i => titulo(i) ? 4 : blocos[i + 1]?.tagName === 'H4' ? 12 : 7;
    let y = M;
    cvs.forEach((cv, i) => {
      let w = L, h = alt(cv);
      if (h > H) { w = L * H / h; h = H }  // rota maior que a folha inteira: diminui para caber
      const junto = titulo(i) && cvs[i + 1] ? Math.min(alt(cvs[i + 1]), H) + vao(i) : 0;  // título não fica sozinho no pé da folha
      if (y > M && y + h + junto > M + H) { pdf.addPage(); y = M }
      pdf.addImage(cv.toDataURL('image/png'), 'PNG', M + (W - w) / 2, y, w, h);
      y += h + vao(i);
    });
  }
  pdf.save(dlgNome + '.pdf'); dmsg.textContent = `Baixado: ${dlgNome}.pdf`;
};
