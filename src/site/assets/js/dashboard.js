// Pathogen Watch — dashboard filtering
// Vanilla JS, no framework. Filters cluster table by pathogen + free-text search.

(function () {
  'use strict';

  const STATE = {
    pathogens: new Set(['Salmonella', 'STEC', 'Listeria', 'Campylobacter']),
    query: '',
  };

  function normalize(s) {
    return (s || '').toLowerCase();
  }

  function applyFilters() {
    const rows = document.querySelectorAll('tr[data-pathogen]');
    let visible = 0;
    const q = normalize(STATE.query);
    rows.forEach((row) => {
      const p = row.getAttribute('data-pathogen');
      const text = normalize(row.getAttribute('data-search') || row.textContent);
      const passesPathogen = STATE.pathogens.has(p);
      const passesQuery = !q || text.includes(q);
      const show = passesPathogen && passesQuery;
      row.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    const countEl = document.getElementById('visible-count');
    if (countEl) countEl.textContent = visible;
  }

  function wirePathogenChips() {
    document.querySelectorAll('.chip[data-pathogen-toggle]').forEach((chip) => {
      const p = chip.getAttribute('data-pathogen-toggle');
      chip.addEventListener('click', () => {
        if (STATE.pathogens.has(p)) {
          STATE.pathogens.delete(p);
          chip.classList.remove('active');
        } else {
          STATE.pathogens.add(p);
          chip.classList.add('active');
        }
        applyFilters();
      });
    });
    const all = document.querySelector('.chip[data-pathogen-all]');
    const none = document.querySelector('.chip[data-pathogen-none]');
    if (all) all.addEventListener('click', () => {
      ['Salmonella', 'STEC', 'Listeria', 'Campylobacter'].forEach(p => STATE.pathogens.add(p));
      document.querySelectorAll('.chip[data-pathogen-toggle]').forEach(c => c.classList.add('active'));
      applyFilters();
    });
    if (none) none.addEventListener('click', () => {
      STATE.pathogens.clear();
      document.querySelectorAll('.chip[data-pathogen-toggle]').forEach(c => c.classList.remove('active'));
      applyFilters();
    });
  }

  function wireSearch() {
    const input = document.getElementById('search-input');
    if (!input) return;
    let t = null;
    input.addEventListener('input', (e) => {
      STATE.query = e.target.value;
      clearTimeout(t);
      t = setTimeout(applyFilters, 80);
    });
  }

  function wireSort() {
    document.querySelectorAll('th.sortable').forEach((th) => {
      th.style.cursor = 'pointer';
      th.addEventListener('click', () => {
        const table = th.closest('table');
        const idx = Array.from(th.parentElement.children).indexOf(th);
        const tbody = table.tBodies[0];
        const rows = Array.from(tbody.querySelectorAll('tr'));
        const asc = th.getAttribute('data-sort-dir') !== 'asc';
        document.querySelectorAll('th[data-sort-dir]').forEach(o => o.removeAttribute('data-sort-dir'));
        th.setAttribute('data-sort-dir', asc ? 'asc' : 'desc');
        rows.sort((a, b) => {
          const av = a.children[idx]?.getAttribute('data-sort') ?? a.children[idx]?.textContent ?? '';
          const bv = b.children[idx]?.getAttribute('data-sort') ?? b.children[idx]?.textContent ?? '';
          const an = parseFloat(av), bn = parseFloat(bv);
          let cmp;
          if (!isNaN(an) && !isNaN(bn)) cmp = an - bn;
          else cmp = av.localeCompare(bv);
          return asc ? cmp : -cmp;
        });
        rows.forEach(r => tbody.appendChild(r));
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    wirePathogenChips();
    wireSearch();
    wireSort();
    applyFilters();
  });
})();
