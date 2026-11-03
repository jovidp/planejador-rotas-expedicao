// Tema escolhido pelo usuário (claro/escuro), guardado no navegador. Carregado no <head> para não piscar.
const temaSalvo = (() => { try { return localStorage.getItem('tema') || ''; } catch { return ''; } })();
if (temaSalvo) document.documentElement.dataset.theme = temaSalvo;
document.addEventListener('DOMContentLoaded', () => {
  const sel = document.getElementById('tema');
  if (!sel) return;
  sel.value = temaSalvo;
  sel.addEventListener('change', () => {
    if (sel.value) document.documentElement.dataset.theme = sel.value; else delete document.documentElement.dataset.theme;
    try { localStorage.setItem('tema', sel.value); } catch {}
  });
});
