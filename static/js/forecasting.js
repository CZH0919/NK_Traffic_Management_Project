(() => {
  'use strict';
  const form = document.getElementById('forecast-form');
  if (form) {
    const level = form.querySelector('[name=level]');
    function showFields() {
      form.querySelectorAll('[data-forecast-target]').forEach(box => {
        box.hidden = box.dataset.forecastTarget !== level.value;
        box.querySelectorAll('input').forEach(input => { input.disabled = box.hidden; });
      });
      const borough = form.querySelector('[data-forecast-borough]');
      borough.hidden = level.value === 'city';
      borough.querySelector('select').disabled = borough.hidden;
    }
    level.addEventListener('change', showFields); showFields();

  }
  const progress = document.getElementById('forecast-progress');
  if (progress && !progress.querySelector('h2').textContent.includes('失败')) {
    let ticks = 0;
    const poll = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(progress.dataset.statusUrl);
        if (!response.ok || response.redirected) throw new Error('状态查询失败，请刷新页面。');
        const data = await response.json();
        if (data.status === 'succeeded' || data.status === 'failed') { clearInterval(timer); location.reload(); return; }
        progress.querySelector('h2').textContent = data.label;
        if (++ticks >= 300) { clearInterval(timer); progress.querySelector('p').textContent = '任务状态保存在数据库中，请稍后刷新查看。'; }
      } catch (_) { clearInterval(timer); progress.querySelector('p').textContent = '状态查询暂时中断，请刷新查看。'; }
    };
    const timer = setInterval(poll, 2000);
  }
  const node = document.getElementById('forecast-chart'), source = document.getElementById('forecast-chart-data');
  if (!node || !source || !window.echarts) return;
  const data = JSON.parse(source.textContent), reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let labels, series, links, zoom;
  const line = (name, values, color, dashed = false) => ({ name, type: 'line', data: values, connectNulls: false, showSymbol: true, symbolSize: 5,
    lineStyle: { color, width: 2, type: dashed ? 'dashed' : 'solid' }, itemStyle: { color }, emphasis: { focus: 'series' } });
  if (data.kind === 'forecast') {
    const history = new Map(data.history.map(row => [row.start, row])), points = new Map(data.points.map(row => [row.period_start, row]));
    labels = [...new Set([...history.keys(), ...points.keys()])].sort();
    links = labels.map(day => history.get(day)?.link);
    const numeric = x => x === null || x === undefined ? null : Number(x);
    const actual = labels.map(day => numeric(history.get(day)?.value));
    const predicted = labels.map(day => numeric(points.get(day)?.estimate));
    const last = data.history.filter(row => row.value !== null).at(-1);
    if (points.size && last) predicted[labels.indexOf(last.start)] = last.value;
    const lower = labels.map(day => numeric(points.get(day)?.lower));
    const width = labels.map(day => { const p = points.get(day); return p?.lower === null || p?.lower === undefined ? null : Number(p.upper)-Number(p.lower); });
    series = [line('已观测完整周期', actual, '#087f83'), line('周期外推', predicted, '#a66a32', true),
      { name: '参考带基底', type: 'line', data: lower, stack: 'error-band', silent: true, showSymbol: false, lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, tooltip: { show: false } },
      { name: '经验误差参考带', type: 'line', data: width, stack: 'error-band', silent: true, showSymbol: false, itemStyle: { color: '#b9854e' }, lineStyle: { opacity: 0 }, areaStyle: { color: '#b9854e', opacity: .18 }, tooltip: { show: false } }];
    zoom = labels.length > 24 ? Math.max(0, 100*(labels.length-24)/labels.length) : 0;
  } else {
    labels = data.rows.map(row => row.period_start); links = data.rows.map(row => row.link);
    series = [line('实际', data.rows.map(r => Number(r.actual)), '#087f83'), line('所选方法', data.rows.map(r => Number(r.predicted)), '#a66a32', true), line('上一期基线', data.rows.map(r => Number(r.baseline)), '#788fa1', true)];
    zoom = 0;
  }
  const chart = echarts.init(node, null, { renderer: 'svg' });
  chart.setOption({ animation: !reduced, animationDuration: 180, aria: { enabled: true, description: '历史、预测与回测数值。缺失留空；下方及历史数值表提供完整数据与支撑入口。' },
    legend: { data: series.filter(s => s.name !== '参考带基底').map(s => s.name), bottom: 36, textStyle: { color: '#486577', fontSize: 11 } },
    grid: { left: 18, right: 48, top: 24, bottom: 92, containLabel: true },
    tooltip: { trigger: 'axis', renderMode: 'richText', confine: true, formatter(items) { return [items[0]?.axisValueLabel || '', ...items.filter(p => !p.seriesName.includes('参考带') && p.value !== null && p.value !== undefined).map(p => `${p.seriesName}：${Number(p.value).toFixed(2)} ${data.unit}`)].join('\n'); } },
    xAxis: { type: 'category', data: labels, boundaryGap: false, axisLabel: { hideOverlap: true, color: '#607887', fontSize: 11, formatter: value => node.dataset.frequency === 'month' ? value.slice(0, 7) : value }, axisLine: { lineStyle: { color: '#cedce4' } } },
    yAxis: { type: 'value', min: 0, name: data.unit, splitLine: { lineStyle: { color: '#e6eef2' } }, axisLabel: { color: '#607887', fontSize: 11 } },
    dataZoom: [{ type: 'slider', start: zoom, end: 100, height: 16, bottom: 4, brushSelect: false }, { type: 'inside', start: zoom, end: 100 }], series });
  chart.on('click', event => { if (event.seriesIndex === 0 && links[event.dataIndex]) location.assign(links[event.dataIndex]); });
  window.TrafficTheme?.watchChart(chart);
  new ResizeObserver(() => chart.resize()).observe(node);
})();
