(() => {
  'use strict';
  document.querySelectorAll('[data-print]').forEach(button => button.addEventListener('click', () => window.print()));
  const report = document.getElementById('report-form');
  if (report) {
    report.querySelector('[name=kind]').addEventListener('change', event => { location.assign('?kind=' + encodeURIComponent(event.target.value)); });
    report.querySelector('[name=saved_query]').addEventListener('change', event => {
      if (event.target.value) location.assign('?saved_query=' + encodeURIComponent(event.target.value));
    });
    const site = report.querySelector('[name=site]');
    if (site) site.addEventListener('change', () => { location.assign('?kind=flow&site=' + encodeURIComponent(site.value)); });
  }
  const exportKind = document.getElementById('export-kind');
  if (exportKind) exportKind.addEventListener('change', () => { location.assign('?kind=' + encodeURIComponent(exportKind.value)); });
  document.querySelectorAll('[data-report-progress]').forEach(panel => {
    let attempts = 0;
    const timer = setInterval(async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(panel.dataset.reportProgress);
        if (!response.ok || response.redirected) throw new Error('status');
        const state = await response.json();
        if (state.status === 'succeeded' || state.status === 'failed') { clearInterval(timer); location.reload(); return; }
        panel.querySelector('h2').textContent = state.label;
        if (++attempts > 900) { clearInterval(timer); panel.querySelector('[data-progress-message]').textContent = '任务仍保存在后台，请稍后刷新查看。'; }
      } catch (_) {
        clearInterval(timer);
        panel.querySelector('[data-progress-message]').textContent = '状态查询暂时中断，请登录或刷新后继续查看。';
      }
    }, 2000);
  });
})();
