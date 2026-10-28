// Load each renderer only when its own map is visible.
(()=>{'use strict';
  const version='20261028-map-ui4';
  for(const node of document.querySelectorAll('[data-analysis-map]')){
    let requested=false;
    const retry=node.closest('.map-workspace').querySelector('[data-map-retry]'),reload=()=>location.reload();
    retry.addEventListener('click',reload);
    async function load(){
      if(requested)return;requested=true;
      try{const [{analysisMap},adapter]=await Promise.all([import(`./analysis_map_view.js?v=${version}`),node.dataset.analysisMap==='statistics'?import(`./statistics_map.js?v=${version}`):import(`./risk_map.js?v=${version}`)]);analysisMap(node,adapter.statisticsAdapter||adapter.riskAdapter);retry.removeEventListener('click',reload);}
      catch(_){requested=false;node.closest('.map-workspace').querySelector('[data-map-status]').textContent='地图暂时无法加载，请刷新页面重试。';}
    }
    const panel=node.closest('details.map-disclosure');
    if(panel){panel.addEventListener('toggle',()=>{if(panel.open)load();});if(new URLSearchParams(location.search).get('map_open')==='1')panel.open=true;if(panel.open)load();}
    else{const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){load();observer.disconnect();}},{rootMargin:'150px'});observer.observe(node);}
  }
})();
