'use strict';
// Planejar semana: grade rotas do cadastro × dias (seg a sáb). Marcar cria (ou reativa); desmarcar uma que já existe cancela.
const plano = $('plano'), pgrade = $('pgrade'), pmsg = $('pmsg');
let plan = null;  // {rotas, dias: [{data, rotas}]} da semana mostrada

function renderPlano() {
  const head = `<thead><tr><th>Rota</th>${plan.dias.map(d => `<th>${DOW[parse(d.data).getDay()]} ${fmt(d.data)}</th>`).join('')}</tr></thead>`;
  const rows = plan.rotas.map(n => `<tr><td>${esc(n)}</td>${plan.dias.map(d => {
    const ja = d.rotas.includes(n);
    return `<td><input type="checkbox" data-d="${d.data}" data-n="${esc(n)}" ${ja ? 'checked data-ja="1" title="Já criada. Desmarque para cancelar."' : ''} aria-label="${esc(n)} em ${fmt(d.data)}"></td>`;
  }).join('')}</tr>`).join('');
  const extras = plan.dias.map(d => d.rotas.filter(n => !plan.rotas.includes(n)));
  const foot = extras.some(e => e.length) ? `<tfoot><tr><td>Fora do cadastro</td>${extras.map(e => `<td>${e.map(esc).join('<br>') || ''}</td>`).join('')}</tr></tfoot>` : '';
  pgrade.innerHTML = head + `<tbody>${rows}</tbody>` + foot;
  contarPlano();
}
const novas = () => [...pgrade.querySelectorAll('input:checked:not([data-ja])')];
const tiradas = () => [...pgrade.querySelectorAll('input[data-ja]:not(:checked)')];
function contarPlano() {
  pgrade.querySelectorAll('input[data-ja]').forEach(cb => cb.classList.toggle('cancelar', !cb.checked));
  const n = novas().length, c = tiradas().length;
  delete $('pcriar').dataset.armed; $('pcriar').textContent = 'Salvar semana';
  pmsg.textContent = n || c ? [n && `${n} rota(s) para criar`, c && `${c} para cancelar (ficam no histórico e podem ser reativadas)`].filter(Boolean).join(' · ')
    : 'Marque as rotas de cada dia. Desmarque uma já criada para cancelar.';
}
$('planbtn').onclick = async () => {
  try { plan = await api(`/api/planejamento/${semana}/`); renderPlano(); $('ptitle').textContent = `Planejar semana ${fmt(plan.dias[0].data)} a ${fmt(plan.dias[5].data)}`; plano.showModal() }
  catch (e) { toast(e.message) }
};
pgrade.onchange = contarPlano;
$('pfechar').onclick = () => plano.close();
$('pcopiar').onclick = async () => {
  try {
    const ant = await api(`/api/planejamento/${addDays(plan.dias[0].data, -7)}/`);
    let n = 0;
    ant.dias.forEach((d, i) => d.rotas.forEach(nome => {
      const cb = pgrade.querySelector(`input[data-d="${plan.dias[i].data}"][data-n="${CSS.escape(nome)}"]`);
      if (cb && !cb.checked) { cb.checked = true; n++ }
    }));
    contarPlano(); if (!n) pmsg.textContent = 'A semana anterior não tem rotas do cadastro que faltem nesta semana.';
  } catch (e) { pmsg.textContent = e.message }
};
$('pcriar').onclick = async () => {
  const marcacoes = {}, desmarcacoes = {}, b = $('pcriar');
  novas().forEach(cb => (marcacoes[cb.dataset.d] ||= []).push(cb.dataset.n));
  tiradas().forEach(cb => (desmarcacoes[cb.dataset.d] ||= []).push(cb.dataset.n));
  if (!novas().length && !tiradas().length) { pmsg.textContent = 'Nada mudou na semana.'; return }
  if (tiradas().length && !b.dataset.armed) { b.dataset.armed = 1; b.textContent = `Confirmar (cancela ${tiradas().length})`; return }  // cancelar pede segundo clique
  try {
    const r = await api('/api/planejar/', { marcacoes, desmarcacoes });
    plano.close(); toast(`Semana salva: ${r.criadas} criada(s), ${r.reativadas} reativada(s), ${r.canceladas} cancelada(s)`);
    await Promise.all([carregarSemana(), carregarDia()]);
  } catch (e) { pmsg.textContent = e.message }
};
