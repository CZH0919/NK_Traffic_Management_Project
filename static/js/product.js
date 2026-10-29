(() => {
  'use strict';
  const setRequired = (field, required) => {
    if (!field) return;
    field.required = required;
    const box = field.closest('[data-field]');
    if (box) {
      box.querySelector('.required-mark').hidden = !required;
      box.querySelector('.optional-mark').hidden = required;
    }
  };
  document.querySelectorAll('datalist[data-for]').forEach(list => {
    document.getElementById(list.dataset.for)?.setAttribute('list', list.id);
  });
  document.querySelectorAll('[data-field]').forEach(box => {
    const input = box.querySelector('input,select,textarea'), help = box.querySelector('.field-help'), error = box.querySelector('.field-errors');
    if (input && (help || error)) input.setAttribute('aria-describedby', [help?.id,error?.id].filter(Boolean).join(' '));
    if (input && error) input.setAttribute('aria-invalid','true');
  });
  document.querySelectorAll('.form-error-summary a').forEach(link => link.addEventListener('click', () => {
    const field = document.getElementById(link.hash.slice(1));
    if (!field) return;
    let parent = field.closest('details');
    while (parent) { parent.open = true; parent = parent.parentElement.closest('details'); }
    field.focus();
  }));
  const error = document.querySelector('.form-error-summary');
  if (error) error.focus({preventScroll:true});

  document.querySelectorAll('form').forEach(form => {
    if (!form.closest('main') || !form.querySelector('.form-field')) return;
    const editing = (form.getAttribute('method') || 'get').toLowerCase() === 'post' && !form.id.includes('forecast') && !form.id.includes('risk') && !form.querySelector('[name=kind]');
    let dirty = false, submitting = false;
    const note = document.createElement('p');
    note.className = 'draft-notice'; note.hidden = true; note.setAttribute('role','status');
    note.textContent = form.id==='report-form' ? '报告设置尚未保存。' : editing ? '内容尚未保存。' : '条件尚未应用；下方仍为上次查询结果。';
    form.append(note);
    const update = event => {
      if (event.target.closest('.save-current') || event.target.type === 'hidden') return;
      dirty = true; note.hidden = false;
    };
    form.addEventListener('input', update); form.addEventListener('change', update);
    form.addEventListener('submit', event => {
      if (event.defaultPrevented) return;
      if (submitting) { event.preventDefault(); return; }
      submitting = true; note.hidden = false;
      note.textContent = (form.getAttribute('method') || 'get').toLowerCase() === 'get' ? '正在查询…' : '正在提交，请稍候…';
      const button = event.submitter;
      if (button) {
        // Keep named submitters in the request; block subsequent clicks without disabling successful controls.
        button.setAttribute('aria-disabled','true');
      }
    });
    if (editing) {
      addEventListener('beforeunload', event => {
        const file = form.querySelector('input[type=file]');
        if (dirty && !submitting && (!file || file.files.length)) { event.preventDefault(); event.returnValue = ''; }
      });
      form.querySelectorAll('a').forEach(link => {
        if (link.textContent.trim() === '取消') link.addEventListener('click', () => { dirty = false; });
      });
    }
    addEventListener('pageshow', () => { submitting = false; form.querySelectorAll('[aria-disabled]').forEach(b => b.removeAttribute('aria-disabled')); note.hidden = !dirty; });
  });

  document.querySelectorAll('form').forEach(form => {
    const dimension = form.querySelector('[name=dimension]'), metric = form.querySelector('select[name=metric]');
    if (!dimension || !metric) return;
    const fixed = {vehicle_type:['records','涉事车辆记录数'], person_type:['records','涉事人员记录数'], injury:['records','涉事人员记录数'], factor:['crashes','涉及事故数']};
    const choices = [['crashes','事故数'],['injured','已报告受伤人数'],['killed','已报告死亡人数']];
    let previous = choices.some(([v])=>v===metric.value) ? metric.value : 'crashes';
    const output = document.createElement('p'); output.className='fixed-metric';
    const hidden = document.createElement('input'); hidden.type='hidden'; hidden.name=metric.name;
    metric.after(output,hidden);
    const apply = () => {
      const item=fixed[dimension.value];
      if (!metric.disabled && choices.some(([v])=>v===metric.value)) previous=metric.value;
      metric.replaceChildren(...(item ? [item] : choices).map(([value,label]) => new Option(label,value)));
      metric.value=item ? item[0] : previous; metric.hidden=metric.disabled=!!item;
      output.hidden=hidden.disabled=!item; output.textContent=item?.[1]||''; hidden.value=item?.[0]||'';
      const optional=metric.closest('[data-field]')?.querySelector('.optional-mark');
      if(optional)optional.hidden=!!item;
    };
    dimension.addEventListener('change',apply); apply();
  });
  const forecast = document.getElementById('forecast-form');
  const appliedMetric=document.querySelector('form[method="get"] select[name="metric"]');
  if(appliedMetric){
    const url=new URL(location.href);
    if(url.searchParams.has('metric') && url.searchParams.get('metric')!==appliedMetric.value){
      url.searchParams.set('metric',appliedMetric.value);history.replaceState(history.state,'',url);
    }
  }
  if (forecast) {
    const sync = () => {
      const level=forecast.elements.level.value;
      ['borough','road','junction'].forEach(name=>setRequired(forecast.elements[name],level===name));
      forecast.elements.horizon.max=forecast.elements.frequency.value==='month'?'12':'26';
    };
    forecast.elements.level.addEventListener('change',sync); forecast.elements.frequency.addEventListener('change',sync); sync();
  }
  document.querySelectorAll('[data-rename-cancel]').forEach(button=>button.addEventListener('click',()=>{
    const detail=button.closest('details'); detail.querySelector('form').reset(); detail.open=false;
  }));
  const matchForm=document.querySelector('[data-match-form]');
  if(matchForm){
    const syncMatch=()=>{
      const confirming=matchForm.elements.action.value==='confirm';
      ['target_kind','target_id','exposure_start','exposure_end'].forEach(name=>{
        const field=matchForm.elements[name];if(!field)return;
        field.disabled=!confirming;field.closest('[data-field]').hidden=!confirming;
        if(['target_kind','target_id'].includes(name))setRequired(field,confirming);
      });
    };
    matchForm.elements.action.addEventListener('change',syncMatch);syncMatch();
  }
  document.querySelectorAll('[data-candidate-id]').forEach(button=>button.addEventListener('click',()=>{
    const form=document.querySelector('[data-match-form]');
    form.elements.action.value='confirm';form.elements.action.dispatchEvent(new Event('change',{bubbles:true}));
    form.elements.target_kind.value=button.dataset.candidateKind;
    form.elements.target_kind.dispatchEvent(new Event('change',{bubbles:true}));
    form.elements.target_id.value=button.dataset.candidateId;
    form.elements.target_id.dispatchEvent(new Event('change',{bubbles:true}));
    form.elements.reason.focus();
  }));
})();
