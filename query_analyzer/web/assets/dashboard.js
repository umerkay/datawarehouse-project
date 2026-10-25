(function () {
  'use strict';
  const search = document.getElementById('search');
  const filter = document.getElementById('filter');
  const rows = Array.from(document.querySelectorAll('#query-rows > tr'));
  function render() {
    let count = 0;
    rows.forEach(function (row) {
      const match = row.dataset.search.includes(search.value.toLowerCase()) &&
        (filter.value === 'all' || row.dataset.signals.split(' ').includes(filter.value));
      row.hidden = !match;
      if (match) count++;
    });
    document.getElementById('query-count').textContent = count + ' of ' + rows.length + ' queries';
  }
  search.addEventListener('input', render);
  filter.addEventListener('change', render);
  document.getElementById('query-controls').hidden = false;
  function openDetail(id) {
    const detail = document.getElementById(id);
    if (detail && detail.classList.contains('query-detail')) {
      detail.open = true;
      detail.scrollIntoView({behavior: 'smooth', block: 'start'});
    }
  }
  document.addEventListener('click', function (event) {
    const link = event.target.closest('[data-query]');
    if (!link) return;
    event.preventDefault();
    openDetail('detail-' + link.dataset.query);
  });
  if (location.hash) openDetail(decodeURIComponent(location.hash.slice(1)));
})();
