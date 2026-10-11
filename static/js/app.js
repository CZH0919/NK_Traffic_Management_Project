'use strict';
(() => {
  const button = document.getElementById('menu-toggle');
  const sidebar = document.getElementById('sidebar');
  const shade = document.getElementById('nav-shade');
  if (!button || !sidebar || !shade) return;
  const setOpen = (open) => {
    sidebar.classList.toggle('is-open', open);
    button.setAttribute('aria-expanded', String(open));
    button.setAttribute('aria-label', open ? '收起导航' : '展开导航');
    shade.hidden = !open;
    if (open) sidebar.querySelector('a').focus();
    else button.focus();
  };
  button.addEventListener('click', () => setOpen(button.getAttribute('aria-expanded') !== 'true'));
  shade.addEventListener('click', () => setOpen(false));
  document.getElementById('nav-close')?.addEventListener('click', () => setOpen(false));
  document.addEventListener('keydown', (event) => {
    if (button.getAttribute('aria-expanded') !== 'true') return;
    if (event.key === 'Escape') setOpen(false);
    if (event.key === 'Tab') {
      const links = sidebar.querySelectorAll('a, button');
      const first = links[0], last = links[links.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  window.matchMedia('(min-width:901px)').addEventListener('change', event => {
    if (event.matches) { sidebar.classList.remove('is-open'); shade.hidden = true; button.setAttribute('aria-expanded', 'false'); }
  });
})();
