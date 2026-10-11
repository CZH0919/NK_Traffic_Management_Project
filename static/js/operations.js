document.querySelectorAll('form').forEach(form => {
  const all = form.querySelector('[data-select-all]');
  const boxes = [...form.querySelectorAll('input[name="selection"]')];
  const count = form.querySelector('[data-selection-count]');
  function sync() {
    const selected = boxes.filter(box => box.checked).length;
    if (count) count.textContent = `已选择 ${selected} 项`;
    if (all) { all.checked = !!boxes.length && selected === boxes.length; all.indeterminate = selected > 0 && selected < boxes.length; }
  }
  if (all) all.addEventListener('change', () => { boxes.forEach(box => { box.checked = all.checked; }); sync(); });
  boxes.forEach(box => box.addEventListener('change', sync));
});
const task = document.querySelector('[data-job-status-url]');
if (task) {
  let failures = 0;
  async function poll() {
    if (document.hidden) { setTimeout(poll, 3000); return; }
    try {
      const response = await fetch(task.dataset.jobStatusUrl, {credentials: 'same-origin', headers: {'Accept': 'application/json'}});
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('status');
      const state = await response.json(); failures = 0;
      task.querySelector('[data-job-label]').textContent = state.label;
      task.querySelector('[data-job-progress]').textContent = `处理行数 ${state.progress_rows} / ${state.total_rows ?? '等待文件校验'}`;
      task.querySelector('[data-worker-state]').textContent = state.worker_online ? '后台处理服务在线' : '后台处理服务未检测到运行；任务等待服务恢复。';
      if (state.finished) { location.reload(); return; }
    } catch (_) {
      failures += 1;
      task.querySelector('[data-worker-state]').textContent = '状态获取暂时失败，已保留已提交的任务。可刷新页面查看。';
    }
    if (failures < 5) setTimeout(poll, 3000);
  }
  setTimeout(poll, 3000);
}
