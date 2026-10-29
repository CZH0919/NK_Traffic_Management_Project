(() => {
  'use strict';
  document.querySelectorAll('[data-print]').forEach(button => button.addEventListener('click', () => window.print()));
  let printDetails;
  addEventListener('beforeprint',()=>{printDetails=[...document.querySelectorAll('.report-document details')].map(node=>[node,node.open]);printDetails.forEach(([node])=>{node.open=true;});});
  addEventListener('afterprint',()=>printDetails?.forEach(([node,open])=>{node.open=open;}));
  const report = document.getElementById('report-form');
  function preserve(form, changed, value) {
    const data=new FormData(form), params=new URLSearchParams();
    const common=['title','date_from','date_to','borough','network','start','end','grain','format'];
    for(const key of common){const item=data.get(key);if(item)params.set(key,item);}
    params.set(changed,value);
    if(changed!=='kind' && changed!=='saved_query')params.set('kind',data.get('kind'));
    if(changed==='kind'){
      if(value==='flow'){if(!params.has('start')&&params.has('date_from'))params.set('start',params.get('date_from'));if(!params.has('end')&&params.has('date_to'))params.set('end',params.get('date_to'));}
      else {if(!params.has('date_from')&&params.has('start'))params.set('date_from',params.get('start'));if(!params.has('date_to')&&params.has('end'))params.set('date_to',params.get('end'));}
    }
    location.assign('?'+params);
  }
  if (report) {
    report.querySelector('[name=kind]').addEventListener('change', event => preserve(report,'kind',event.target.value));
    report.querySelector('[name=saved_query]').addEventListener('change', event => {
      if (event.target.value) preserve(report,'saved_query',event.target.value);
    });
    const site = report.querySelector('[name=site]');
    if (site) site.addEventListener('change', () => preserve(report,'site',site.value));
  }
  const exportKind = document.getElementById('export-kind');
  if (exportKind) exportKind.addEventListener('change', () => preserve(exportKind.form,'kind',exportKind.value));
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
