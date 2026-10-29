(() => {
  'use strict';
  const aliases = document.getElementById('help-aliases');
  if (aliases && location.hash) {
    const mapping = JSON.parse(aliases.textContent), target = mapping[location.hash.slice(1)];
    if (target && target !== '/help/') { location.replace(target); return; }
  }
  const menu = document.querySelector('.help-topic-menu');
  if (menu && matchMedia('(max-width: 900px)').matches) menu.open = false;
  function reveal() {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch (_) { return; }
    const target = id && document.getElementById(id);
    if (target) target.querySelectorAll('details').forEach(node => { node.open = true; });
  }
  reveal(); addEventListener('hashchange', reveal);
  let printState;
  addEventListener('beforeprint', () => {
    printState = [...document.querySelectorAll('.guide-body details')].map(node => [node, node.open]);
    printState.forEach(([node]) => { node.open = true; });
  });
  addEventListener('afterprint', () => printState?.forEach(([node, open]) => { node.open = open; }));
  document.querySelector('[data-help-print]')?.addEventListener('click', () => print());
})();
