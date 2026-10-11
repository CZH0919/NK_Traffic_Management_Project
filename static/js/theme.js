/* Apply the saved appearance before stylesheets load; no server or data changes. */
(() => {
  'use strict';
  const key = 'traffic-ui-theme', root = document.documentElement;
  let selected = 'light';
  try { if (localStorage.getItem(key) === 'dark') selected = 'dark'; } catch (_) { /* Storage may be unavailable. */ }
  const colors = {
    light: { land: '#f8faf7', edge: '#a6c1ca', road: '#6c8393', border: '#ffffff', junction: '#49688c', site: '#087f83', crash: '#b76524', selected: '#056d75', halo: '#a8dddd' },
    dark: { land: '#253d4a', edge: '#496473', road: '#869eac', border: '#142a37', junction: '#a3bfd5', site: '#61c9c1', crash: '#e4a268', selected: '#87e0d5', halo: '#499c9c' }
  };
  function apply(value) {
    selected = value === 'dark' ? 'dark' : 'light';
    root.dataset.theme = selected;
    root.dataset.bsTheme = selected;
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.setAttribute('aria-pressed', String(selected === 'dark'));
      button.title = selected === 'dark' ? '当前为深色，切换为浅色' : '当前为浅色，切换为深色';
      button.querySelector('[data-theme-label]').textContent = selected === 'dark' ? '深色' : '浅色';
    });
  }
  apply(selected);
  function change(value, persist = true) {
    apply(value);
    if (persist) { try { localStorage.setItem(key, selected); } catch (_) { /* Keep the choice for this page. */ } }
    document.dispatchEvent(new CustomEvent('traffic:themechange', { detail: { theme: selected } }));
  }
  const darkColors = { '#087f83': '#61c9c1', '#a76530': '#e4a268', '#b56a2c': '#e4a268', '#a66a32': '#e4a268', '#b9854e': '#e4ad75', '#788fa1': '#a2b6cb' };
  function watchChart(chart) {
    const original = chart.getOption();
    function paint(dark) {
      if (chart.isDisposed()) return;
      const ink = dark ? '#dbe8ee' : '#486577', muted = dark ? '#acbfcc' : '#607887';
      const line = dark ? '#3c5361' : '#cedce4', grid = dark ? '#2d4352' : '#e6eef2';
      const recolor = value => dark ? (darkColors[value] || value) : value;
      const patch = {
        color: original.color.map(recolor), textStyle: { color: ink },
        tooltip: { backgroundColor: dark ? '#213846' : '#ffffff', borderColor: line, textStyle: { color: ink } },
        series: original.series.map(series => {
          const style = { label: { color: ink } };
          for (const name of ['lineStyle', 'itemStyle', 'areaStyle']) {
            if (series[name]?.color) style[name] = { color: recolor(series[name].color) };
          }
          return style;
        })
      };
      for (const axis of ['xAxis', 'yAxis']) if (original[axis]?.length) {
        patch[axis] = original[axis].map(() => ({ axisLabel: { color: muted }, nameTextStyle: { color: muted }, axisLine: { lineStyle: { color: line } }, axisTick: { lineStyle: { color: line } }, splitLine: { lineStyle: { color: grid } } }));
      }
      if (original.legend?.length) patch.legend = original.legend.map(() => ({ textStyle: { color: ink }, inactiveColor: dark ? '#708494' : '#888888' }));
      if (original.dataZoom?.length) patch.dataZoom = original.dataZoom.map(zoom => zoom.type === 'slider' ? { textStyle: { color: muted }, borderColor: line, backgroundColor: dark ? '#142735' : '#f4f8fa', fillerColor: dark ? '#61c9c130' : '#d8eceb', dataBackground: { lineStyle: { color: muted }, areaStyle: { color: muted } }, selectedDataBackground: { lineStyle: { color: dark ? '#61c9c1' : '#087f83' }, areaStyle: { color: dark ? '#61c9c1' : '#087f83' } }, handleStyle: { color: dark ? '#acbfcc' : '#ffffff', borderColor: line }, moveHandleStyle: { color: muted } } : {});
      // Merge styles only: preserve selected legends, zoom, values and drill-down events.
      chart.setOption(patch);
    }
    paint(selected === 'dark');
    document.addEventListener('traffic:themechange', () => paint(selected === 'dark'));
    window.addEventListener('beforeprint', () => paint(false));
    window.addEventListener('afterprint', () => paint(selected === 'dark'));
  }
  window.TrafficTheme = { watchChart, mapPalette: () => colors[selected] };
  window.addEventListener('storage', event => { if (event.key === key) change(event.newValue, false); });
  document.addEventListener('DOMContentLoaded', () => {
    apply(selected);
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.hidden = false;
      button.addEventListener('click', () => change(selected === 'dark' ? 'light' : 'dark'));
    });
  });
})();
