'use strict';
// Rotas do cadastro: tirar, acrescentar e reordenar as lojas de uma rota (vale para as rotas criadas daqui para frente).
const rcad = $('rcad'), rcrota = $('rcrota'), rclista = $('rclista'), rcmsg = $('rcmsg');
let rcLojas = [];

function rcRender() {
  rclista.innerHTML = '<thead><tr><th>Ordem</th><th>Loja</th><th>PES</th><th>EMP</th><th></th></tr></thead><tbody>'
    + rcLojas.map((n, i) => `<tr><td>${i + 1}º</td><td><b>${esc(n)}</b></td><td>${esc(cad.lojas[n]?.pes || '')}</td><td>${esc(cad.lojas[n]?.emp || '')}</td>
      <td><span class="acts"><button data-i="${i}" data-m="-1" title="Subir" aria-label="Subir ${esc(n)}">↑</button><button data-i="${i}" data-m="1" title="Descer" aria-label="Descer ${esc(n)}">↓</button><button data-i="${i}" data-m="x" title="Tirar da rota" aria-label="Tirar ${esc(n)} da rota">x</button></span></td></tr>`).join('')
    + (rcLojas.length ? '' : '<tr><td colspan="5">Nenhuma loja nesta rota.</td></tr>') + '</tbody>';
}
function rcAbrir() { rcLojas = [...(cad.rotas[rcrota.value]?.lojas || [])]; rcmsg.textContent = ''; rcRender() }

$('rcadbtn').onclick = () => {
  rcrota.innerHTML = Object.keys(cad.rotas).sort().map(n => `<option>${esc(n)}</option>`).join('');
  rcAbrir(); rcad.showModal();
};
rcrota.onchange = rcAbrir;
rclista.onclick = e => {
  const b = e.target.closest('button[data-i]'); if (!b) return;
  const i = +b.dataset.i;
  if (b.dataset.m === 'x') { rcmsg.textContent = `${rcLojas[i]} sai da rota ao salvar (continua no cadastro de lojas).`; rcLojas.splice(i, 1) }
  else { const j = i + +b.dataset.m; if (j < 0 || j >= rcLojas.length) return; [rcLojas[i], rcLojas[j]] = [rcLojas[j], rcLojas[i]] }
  rcRender();
};
$('rcform').onsubmit = e => {
  e.preventDefault();
  const n = $('rcloja').value.trim().toUpperCase(); if (!n) return;
  if (!cad.lojas[n]) { rcmsg.textContent = `${n} não está no cadastro de lojas.`; return }
  if (rcLojas.includes(n)) { rcmsg.textContent = `${n} já está nesta rota.`; return }
  const outra = cad.lojas[n].rota;
  rcLojas.push(n); $('rcloja').value = ''; rcRender();
  rcmsg.textContent = outra && outra !== rcrota.value ? `${n} sai da ${outra} ao salvar.` : `${n} entra no fim da rota ao salvar.`;
};
$('rcsalvar').onclick = async () => {
  try {
    cad = await api('/api/cadastro/rota/', { rota: rcrota.value, lojas: rcLojas });
    listasCadastro(); addPreview(); rcAbrir();  // "Adicionar rota" já mostra as lojas novas
    rcmsg.textContent = `Rota ${rcrota.value} salva. Vale para as rotas criadas daqui para frente.`;
  } catch (e) { rcmsg.textContent = e.message }
};
$('rcfechar').onclick = () => rcad.close();
