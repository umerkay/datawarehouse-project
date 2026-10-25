(function () {
  'use strict';

  // Plan counts and timings remain separate; missing evidence never becomes zero.
  function metricValue(data, baseline, metric) {
    if (metric === 'time') return data.ms === null ? null : data.ms / 1000;
    if (metric === 'growth') return baseline > 0 && data.ms !== null ? data.ms / baseline : null;
    return data[metric];
  }

  // Select one query and connect links from both explorers.
  function initQueryNavigation() {
    const selector = document.getElementById('query-select');
    const panels = Array.from(document.querySelectorAll('.query-comparison'));
    function choose(query, scroll) {
      if (!panels.some(function (p) { return p.dataset.query === query; })) return;
      selector.value = query;
      panels.forEach(function (p) { p.hidden = p.dataset.query !== query; });
      if (scroll) document.getElementById('compare-' + query).scrollIntoView({behavior:'smooth',block:'start'});
    }
    selector.addEventListener('change', function () { choose(selector.value, false); });
    document.addEventListener('click', function (event) {
      const link = event.target.closest('[data-select]');
      if (link) { event.preventDefault(); choose(link.dataset.select, true); }
    });
    const search = document.getElementById('query-search');
    const timingRows = Array.from(document.querySelectorAll('#timing-rows > tr'));
    search.addEventListener('input', function () {
      timingRows.forEach(function (row) { row.hidden = !row.dataset.search.includes(search.value.toLowerCase()); });
    });
    document.getElementById('comparison-controls').hidden = false;
    const fromHash = location.hash.replace('#compare-', '');
    choose(panels.some(function (p) {return p.dataset.query === fromHash;}) ? fromHash : (panels.some(function (p) {return p.dataset.query === 'query_015';}) ? 'query_015' : selector.value), false);
  }

  // Filter the pre-rendered structure comparison matrix.
  function initPlanMap() {
    const planFilter = document.getElementById('plan-map-filter');
    const planSearch = document.getElementById('plan-map-search');
    const rows = Array.from(document.querySelectorAll('#plan-map-rows > tr'));
    function filterPlans() {
      rows.forEach(function (row) {
        row.hidden = !row.dataset.planQuery.toLowerCase().includes(planSearch.value.trim().toLowerCase()) ||
          (planFilter.value !== 'all' && !row.dataset.planStates.split(' ').includes(planFilter.value));
      });
      document.getElementById('plan-map-status').textContent = rows.filter(function (row) { return !row.hidden; }).length + ' of ' + rows.length + ' queries shown';
    }
    planFilter.addEventListener('input', filterPlans);
    planSearch.addEventListener('input', filterPlans);
    document.getElementById('plan-map-controls').hidden = false;
    filterPlans();
  }

  // Rank and draw historical timings or captured plan evidence.
  function initWorkload() {
    const workload = JSON.parse(document.getElementById('workload-data').textContent);
    const controls = ['scale', 'metric', 'order', 'limit', 'search'].reduce(function (result, key) {
      result[key] = document.getElementById('workload-' + key);
      return result;
    }, {});
    function drawWorkload() {
      const scale = controls.scale.value;
      const metric = controls.metric.value;
      const searchText = controls.search.value.trim().toLowerCase();
      const matched = workload.filter(function (row) { return row.query.toLowerCase().includes(searchText); });
      const ranked = matched.map(function (row) {
        const data = row.scales[scale];
        const base = row.scales['1GB'].ms;
        const value = metricValue(data, base, metric);
        return {query:row.query, value:value};
      }).filter(function (row) { return row.value !== null && Number.isFinite(row.value); });
      ranked.sort(function (a, b) {
        return (controls.order.value === 'asc' ? a.value - b.value : b.value - a.value) || a.query.localeCompare(b.query);
      });
      const shown = controls.limit.value === 'all' ? ranked : ranked.slice(0, Number(controls.limit.value));
      const maximum = Math.max(0, ...shown.map(function (row) { return row.value; }));
      const container = document.getElementById('workload-bars');
      container.replaceChildren();
      shown.forEach(function (row) {
        const link = document.createElement('a');
        link.href = '#compare-' + row.query;
        link.dataset.select = row.query;
        link.className = 'workload-row';
        const name = document.createElement('span');
        name.textContent = row.query;
        const track = document.createElement('span');
        track.className = 'track';
        track.setAttribute('aria-hidden', 'true');
        const bar = document.createElement('span');
        bar.className = 'bar';
        bar.style.width = (maximum ? row.value / maximum * 100 : 0) + '%';
        track.append(bar);
        const value = document.createElement('b');
        value.textContent = row.value.toLocaleString('en', {maximumFractionDigits: metric === 'time' || metric === 'growth' ? 3 : 0}) +
          (metric === 'time' ? ' s' : metric === 'growth' ? '×' : '');
        link.append(name, track, value);
        container.append(link);
      });
      document.getElementById('workload-status').textContent = shown.length + ' of ' + ranked.length +
        ' queries with evidence shown · ' + (matched.length - ranked.length) + ' excluded for missing evidence · ' +
        scale + ' · ' + controls.metric.selectedOptions[0].textContent + (shown.length ? '' : '. No matching data; try another dataset or metric.');
    }
    Object.values(controls).forEach(function (control) { control.addEventListener('input', drawWorkload); });
    document.getElementById('workload-panel').hidden = false;
    drawWorkload();
  }

  initQueryNavigation();
  initPlanMap();
  initWorkload();
})();
