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
  const labels = {road: '道路', junction: '路口', site: '采样地点', collision:'事故', vehicle:'车辆', risk_run:'风险计算', forecast_run:'预测计算'};
  document.querySelectorAll('[data-object-picker]').forEach(picker => {
    const input = picker.querySelector('input'), form = input.form;
    let kind = picker.dataset.objectPicker;
    let label = labels[kind] || '对象';
    const scopeName=picker.dataset.pickerScope;
    const trigger = picker.querySelector('[data-picker-open]');
    const status = picker.querySelector('[data-picker-selection]');
    const manual = picker.querySelector('details');
    trigger.hidden = false;
    manual.open = !!picker.parentElement.querySelector('.field-errors');
    let selectionRequest = 0, selectionController;
    const network = () => form.querySelector('[name=network]')?.value || '';
    const endpoint = picker.dataset.endpoint;
    const showSelection = async () => {
      const ticket = ++selectionRequest;
      selectionController?.abort(); selectionController=new AbortController();
      status.textContent = input.value ? '正在确认所选对象…' : picker.dataset.pickerEmpty || `尚未选择${label}`;
      const clear=picker.querySelector('[data-picker-clear]'); if(clear)clear.hidden=!input.value;
      if (!input.value) return;
      try {
        const url = new URL(endpoint, location.origin);
        url.search = new URLSearchParams({kind, id:input.value, network:network(), collision_id:scopeName ? form.elements[scopeName]?.value||'' : ''});
        const response = await fetch(url, {signal:selectionController.signal,headers:{Accept:'application/json'}});
        if (!response.ok || response.redirected) throw new Error();
        const data = await response.json();
        if (ticket !== selectionRequest) return;
        const item = data.items[0];
        status.textContent = item ? `${item.name} · ${item.detail}${kind.endsWith('_run')?'':` · 内部编号 #${item.id}`}` : '该编号不在当前有效范围，请重新选择。';
      } catch (error) { if (error.name!=='AbortError' && ticket === selectionRequest) status.textContent = '对象信息暂不可用，可重新搜索或核对编号。'; }
    };
    input.addEventListener('change', showSelection);
    picker.querySelector('[data-picker-clear]')?.addEventListener('click',()=>{input.value='';input.dispatchEvent(new Event('change',{bubbles:true}));});
    if(scopeName)form.elements[scopeName]?.addEventListener('change',()=>{if(input.value){input.value='';input.dispatchEvent(new Event('change',{bubbles:true}));status.textContent='事故条件已更换，请重新核对车辆。';}});
    if(input.name==='target_id')form.elements.target_kind?.addEventListener('change',()=>{kind=form.elements.target_kind.value;label=labels[kind];input.value='';showSelection();});
    form.querySelector('[name=network]')?.addEventListener('change', () => {
      if (!['road','junction'].includes(kind)) return;
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
      const searchLabel = document.createElement('label'); searchLabel.textContent = kind.endsWith('_run')?'对象名称、范围起止日期或完整计算编号':`${label}名称、日期或来源编号`;
      const search = document.createElement('input'); search.className='form-control'; search.type='search'; search.maxLength=200;
      searchLabel.append(search);
      const submit = document.createElement('button'); submit.type='submit'; submit.className='btn btn-primary'; submit.textContent='搜索';
      searchForm.append(searchLabel,submit);
      const note = document.createElement('p'); note.className='section-note'; note.textContent=kind.endsWith('_run')?'按对象与期间选择已完成的计算。':'核对名称、来源编号和位置后选择；同名对象可能是不同记录。';
      const feedback = document.createElement('p'); feedback.setAttribute('role','status'); feedback.textContent='请输入至少两个字符。';
      const results = document.createElement('div'); results.className='picker-results';
      const pages = document.createElement('div'); pages.className='form-actions';
      const prev = document.createElement('button'), next = document.createElement('button');
      [prev,next].forEach(b=>{b.type='button'; b.className='btn btn-outline-primary'; b.disabled=true;});
      prev.textContent='上一页'; next.textContent='下一页'; pages.append(prev,next);
      dialog.append(head,note,searchForm,feedback,results,pages); document.body.append(dialog);
      let page=1, query='', pending=0, controller, debounce;
      const finish=()=>{clearTimeout(debounce);controller?.abort(); dialog.remove(); trigger.focus();};
      close.addEventListener('click',()=>dialog.close()); dialog.addEventListener('close',finish,{once:true});
      async function load() {
        const ticket=++pending; controller?.abort(); controller=new AbortController();
        results.replaceChildren(); feedback.textContent='正在查找…'; prev.disabled=next.disabled=true;
        const url = new URL(endpoint, location.origin);
        url.search = new URLSearchParams({kind,q:query,page,network:network(),collision_id:scopeName ? form.elements[scopeName]?.value||'' : ''});
        try {
          const response=await fetch(url,{signal:controller.signal,headers:{Accept:'application/json'}});
          if (response.redirected) throw Object.assign(new Error(),{userMessage:'请重新登录后选择对象。'});
          const data=await response.json();
          if (!response.ok) throw Object.assign(new Error(),{userMessage:data.error||'查询暂不可用，请重试。'});
          if(ticket!==pending || !dialog.isConnected) return;
          feedback.textContent=data.message||`第 ${page} 页 · 本页 ${data.items.length} 个对象`;
          for(const item of data.items) {
            const card=document.createElement('article'); card.className='picker-result';
            const name=document.createElement('h3'); name.textContent=item.name;
            const detail=document.createElement('p'); detail.textContent=item.detail;
            const meta=document.createElement('p'); meta.className='section-note'; meta.textContent=kind.endsWith('_run')?`计算编号 ${item.id}`:[`来源编号 ${item.source_id}`,`内部编号 #${item.id}`,item.network_label].filter(Boolean).join(' · ');
            const choose=document.createElement('button'); choose.type='button'; choose.className='btn btn-primary btn-sm'; choose.textContent='选择此'+label;
            choose.setAttribute('aria-label',`选择 ${item.name}，内部编号 ${item.id}`);
            choose.addEventListener('click',()=>{input.value=item.id; input.dispatchEvent(new Event('change',{bubbles:true})); dialog.close();});
            const map=document.createElement('a'); map.href=item.map_url||item.url; map.target='_blank'; map.rel='noopener'; map.textContent=item.map_url?'地图查看（新窗口）':'查看详情（新窗口）';
            const actions=document.createElement('div'); actions.className='form-actions'; actions.append(choose,map);
            card.append(name,detail,meta,actions); results.append(card);
          }
          prev.disabled=page===1; next.disabled=!data.has_more;
        } catch(error) {if(error.name!=='AbortError' && ticket===pending) feedback.textContent=error.userMessage||'暂时无法连接查询服务，请再次点击“搜索”。';}
      }
      searchForm.addEventListener('submit',e=>{e.preventDefault();clearTimeout(debounce);query=search.value.trim();page=1;load();});
      search.addEventListener('input',()=>{clearTimeout(debounce);controller?.abort();pending++;query=search.value.trim();page=1;if(query.length>=2)debounce=setTimeout(load,300);else{results.replaceChildren();prev.disabled=next.disabled=true;feedback.textContent='请输入至少两个字符。';}});
      prev.addEventListener('click',()=>{page--;load();}); next.addEventListener('click',()=>{page++;load();});
      dialog.showModal(); search.focus();
      if(kind.endsWith('_run') || (kind==='vehicle' && form.elements[scopeName]?.value))load();
    });
  });
  const uploadSource=document.querySelector('[data-upload-source]');
  const schemas=document.getElementById('upload-schemas');
  if(uploadSource && schemas) {
    const values=JSON.parse(schemas.textContent), box=document.getElementById('selected-upload-schema');
    const show=()=>{
      const item=values[uploadSource.value];box.textContent=item ? `${item.label} · 下表列出必需字段及常用可选字段。` : '请先选择官方数据来源，查看该文件必须包含的字段。';
      const target=document.getElementById('selected-upload-fields');if(!target)return;target.replaceChildren();if(!item)return;
      const link=document.createElement('a');link.href=item.headers_url;link.className='btn btn-outline-primary btn-sm';link.textContent='下载字段表头（不含数据）';target.append(link);
      const details=document.createElement('details');details.open=true;const summary=document.createElement('summary');summary.textContent='字段含义与格式';details.append(summary);
      const wrap=document.createElement('div');wrap.className='table-responsive';const table=document.createElement('table');table.className='table';
      const head=document.createElement('thead'),header=document.createElement('tr');['字段','API 表头','要求','格式与说明'].forEach(text=>{const th=document.createElement('th');th.textContent=text;header.append(th);});head.append(header);table.append(head);
      const body=document.createElement('tbody');for(const row of item.rows){const tr=document.createElement('tr');[row.label,row.key,row.required?'必需表头':'可选',row.example].forEach(text=>{const td=document.createElement('td');td.textContent=text;tr.append(td);});body.append(tr);}table.append(body);wrap.append(table);details.append(wrap);target.append(details);
    };
    uploadSource.addEventListener('change',show);show();
  }
})();
