(() => {
  'use strict';
  // Disclosures open automatically when they contain errors or explicit filters.
  document.querySelectorAll('details.advanced-filters').forEach(panel => {
    if (panel.hasAttribute('data-server-disclosure')) return;
    const params = new URLSearchParams(location.search);
    const selected = [...panel.querySelectorAll('[name]')].some(field => params.has(field.name) && params.get(field.name) && !['metric','rule','network'].includes(field.name));
    if (selected || panel.querySelector('.field-errors')) panel.open = true;
  });
  document.querySelectorAll('form').forEach(form => form.addEventListener('invalid', event => {
    let panel=event.target.closest('details');
    while(panel){panel.open=true;panel=panel.parentElement.closest('details');}
  },true));
  const labels = {road: '道路', junction: '路口', site: '采样地点'};
  document.querySelectorAll('[data-object-picker]').forEach(picker => {
    const input = picker.querySelector('input'), form = input.form;
    const kind = picker.dataset.objectPicker, label = labels[kind];
    const trigger = picker.querySelector('[data-picker-open]');
    const status = picker.querySelector('[data-picker-selection]');
    const manual = picker.querySelector('details');
    trigger.hidden = false;
    manual.open = !!picker.parentElement.querySelector('.field-errors');
    let selectionRequest = 0;
    const network = () => form.querySelector('[name=network]')?.value || '';
    const endpoint = picker.dataset.endpoint;
    const showSelection = async () => {
      const ticket = ++selectionRequest;
      status.textContent = input.value ? '正在确认所选对象…' : `尚未限定${label}`;
      if (!input.value) return;
      try {
        const url = new URL(endpoint, location.origin);
        url.search = new URLSearchParams({kind, id:input.value, network:network()});
        const response = await fetch(url, {headers:{Accept:'application/json'}});
        if (!response.ok || response.redirected) throw new Error();
        const data = await response.json();
        if (ticket !== selectionRequest) return;
        const item = data.items[0];
        status.textContent = item ? `${item.name} · ${item.detail} · 内部编号 #${item.id}` : '该编号不在当前有效范围，请重新选择。';
      } catch (_) { if (ticket === selectionRequest) status.textContent = '对象信息暂不可用，请检查网络或手动核对编号。'; }
    };
    input.addEventListener('change', showSelection);
    form.querySelector('[name=network]')?.addEventListener('change', () => {
      if (kind === 'site') return;
      selectionRequest++;
      if (input.value) {
        input.value = '';
        status.textContent = '网络版本已更换，请重新选择对象。';
      }
    });
    showSelection();
    trigger.addEventListener('click', () => {
      const dialog = document.createElement('dialog');
      dialog.className = 'object-dialog';
      dialog.setAttribute('aria-label', `按名称选择${label}`);
      const heading = document.createElement('h2'); heading.textContent = `选择${label}`;
      const close = document.createElement('button'); close.type='button'; close.className='btn btn-outline-primary'; close.textContent='关闭';
      const head = document.createElement('div'); head.className='section-heading'; head.append(heading,close);
      const searchForm = document.createElement('form'); searchForm.className='picker-search';
      const searchLabel = document.createElement('label'); searchLabel.textContent = `${label}名称或来源编号`;
      const search = document.createElement('input'); search.className='form-control'; search.type='search'; search.maxLength=200; search.minLength=2; search.required=true;
      searchLabel.append(search);
      const submit = document.createElement('button'); submit.type='submit'; submit.className='btn btn-primary'; submit.textContent='搜索';
      searchForm.append(searchLabel,submit);
      const note = document.createElement('p'); note.className='section-note'; note.textContent='核对来源编号及位置，同名对象按独立记录选择。道路与路口保留当前网络版本；来源未提供行政区时如实标为未知。';
      const feedback = document.createElement('p'); feedback.setAttribute('role','status'); feedback.textContent='请输入至少两个字符。';
      const results = document.createElement('div'); results.className='picker-results';
      const pages = document.createElement('div'); pages.className='form-actions';
      const prev = document.createElement('button'), next = document.createElement('button');
      [prev,next].forEach(b=>{b.type='button'; b.className='btn btn-outline-primary'; b.disabled=true;});
      prev.textContent='上一页'; next.textContent='下一页'; pages.append(prev,next);
      dialog.append(head,note,searchForm,feedback,results,pages); document.body.append(dialog);
      let page=1, query='', pending=0, controller;
      const finish=()=>{controller?.abort(); dialog.remove(); trigger.focus();};
      close.addEventListener('click',()=>dialog.close()); dialog.addEventListener('close',finish,{once:true});
      async function load() {
        const ticket=++pending; controller?.abort(); controller=new AbortController();
        results.replaceChildren(); feedback.textContent='正在查找…'; prev.disabled=next.disabled=true;
        const url = new URL(endpoint, location.origin);
        url.search = new URLSearchParams({kind,q:query,page,network:network()});
        try {
          const response=await fetch(url,{signal:controller.signal,headers:{Accept:'application/json'}});
          if (response.redirected) throw new Error('请重新登录后选择对象。');
          const data=await response.json();
          if (!response.ok) throw new Error(data.error||'查询暂不可用，请重试。');
          if(ticket!==pending || !dialog.isConnected) return;
          feedback.textContent=data.message||`第 ${page} 页 · 本页 ${data.items.length} 个对象`;
          for(const item of data.items) {
            const card=document.createElement('article'); card.className='picker-result';
            const name=document.createElement('h3'); name.textContent=item.name;
            const detail=document.createElement('p'); detail.textContent=item.detail;
            const meta=document.createElement('p'); meta.className='section-note'; meta.textContent=`来源编号 ${item.source_id} · 内部编号 #${item.id} · ${item.network_label}`;
            const choose=document.createElement('button'); choose.type='button'; choose.className='btn btn-primary btn-sm'; choose.textContent='选择此'+label;
            choose.setAttribute('aria-label',`选择 ${item.name}，内部编号 ${item.id}`);
            choose.addEventListener('click',()=>{input.value=item.id; input.dispatchEvent(new Event('change',{bubbles:true})); dialog.close();});
            const map=document.createElement('a'); map.href=item.map_url; map.target='_blank'; map.rel='noopener'; map.textContent='地图查看（新窗口）';
            const actions=document.createElement('div'); actions.className='form-actions'; actions.append(choose,map);
            card.append(name,detail,meta,actions); results.append(card);
          }
          prev.disabled=page===1; next.disabled=!data.has_more;
        } catch(error) {if(error.name!=='AbortError' && ticket===pending) feedback.textContent=error.message||'查询暂不可用，请重试。';}
      }
      searchForm.addEventListener('submit',e=>{e.preventDefault();query=search.value.trim();page=1;load();});
      prev.addEventListener('click',()=>{page--;load();}); next.addEventListener('click',()=>{page++;load();});
      dialog.showModal(); search.focus();
    });
  });
  const uploadSource=document.querySelector('[data-upload-source]');
  const schemas=document.getElementById('upload-schemas');
  if(uploadSource && schemas) {
    const values=JSON.parse(schemas.textContent), box=document.getElementById('selected-upload-schema');
    const show=()=>{const item=values[uploadSource.value];box.textContent=item ? `${item.label} · 必需字段：${item.fields}` : '请先选择官方数据来源，查看该文件必须包含的字段。';};
    uploadSource.addEventListener('change',show);show();
  }
})();
