'use strict';
// Aba Veículos: frota com o tipo (Accelo/Atego) que define o frete. Qualquer usuário cadastra carro novo.
const veic = $('veic'), vlista = $('vlista'), vmsg = $('vmsg');
const TIPOS = { accelo: 'Accelo (2 eixos)', atego: 'Atego (3 eixos)' };

function renderVeiculos(vs) {
  vlista.innerHTML = `<thead><tr><th>Placa</th><th>Tipo</th><th>Descrição</th><th></th></tr></thead><tbody>` + vs.map(v => `<tr>
    <td class="mono"><b>${esc(v.placa)}</b></td>
    <td><select data-placa="${esc(v.placa)}" data-desc="${esc(v.descricao)}" aria-label="Tipo de ${esc(v.placa)}">${Object.entries(TIPOS).map(([k, l]) => `<option value="${k}" ${k === v.tipo ? 'selected' : ''}>${l}</option>`).join('')}</select></td>
    <td>${esc(v.descricao)}</td>
    <td><button class="btn sm" data-remover="${esc(v.placa)}">Remover</button></td></tr>`).join('') + '</tbody>';
}
async function salvarVeiculo(placa, tipo, descricao) {
  try { renderVeiculos((await api('/api/veiculos/', { placa, tipo, descricao })).veiculos); vmsg.textContent = `${placa.toUpperCase().replace(/[^A-Z0-9]/g, '')} salvo como ${TIPOS[tipo]}.`; await carregarDia(); return true }
  catch (e) { vmsg.textContent = e.message; return false }
}
$('veicbtn').onclick = async () => {
  vmsg.textContent = '';
  try { renderVeiculos((await api('/api/veiculos/')).veiculos); veic.showModal(); $('vplaca').focus() } catch (e) { toast(e.message) }
};
$('vfechar').onclick = () => veic.close();
$('vform').onsubmit = async e => {
  e.preventDefault();
  if (await salvarVeiculo($('vplaca').value, $('vtipo').value, $('vdesc').value)) { $('vplaca').value = ''; $('vdesc').value = ''; $('vplaca').focus() }
};
vlista.onchange = e => { const s = e.target.closest('select[data-placa]'); if (s) salvarVeiculo(s.dataset.placa, s.value, s.dataset.desc) };
vlista.onclick = async e => {
  const b = e.target.closest('[data-remover]'); if (!b) return;
  if (!b.dataset.armed) { b.dataset.armed = 1; b.textContent = 'Confirmar?'; setTimeout(() => { if (b.isConnected) { delete b.dataset.armed; b.textContent = 'Remover' } }, 3000); return }
  try { renderVeiculos((await api('/api/veiculos/remover/', { placa: b.dataset.remover })).veiculos); vmsg.textContent = `${b.dataset.remover} removido.` } catch (err) { vmsg.textContent = err.message }
};
