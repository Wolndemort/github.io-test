(() => {
  const root = document.querySelector('#staff-table');
  if (!root) return;
  const esc = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const api = url => fetch(url).then(async response => { const data = await response.json().catch(() => ({})); if (!response.ok) throw Error(data.detail?.message || `HTTP ${response.status}`); return data; });
  const list = (title, rows, empty) => `<details class="table-section" open><summary>${title} <span class="web-status">(${rows.length})</span></summary><div class="table-list">${rows.map(row => `<div class="table-list-row"><div><strong>${esc(row.name)}</strong><small>${esc(row.last_visit || row.session_end || row.discipline || '')}</small></div><span>${esc(row.balance ?? row.mins_left ?? '')}</span></div>`).join('') || `<p class="web-empty">${empty}</p>`}</div></details>`;
  const render = data => {
    const m = data.metrics || {};
    const all = m.all_athletes || [];
    root.innerHTML = `<section class="web-card table-dashboard"><div class="table-toolbar"><label>Быстрый поиск атлета<input data-search type="search" placeholder="Введите имя атлета…" autocomplete="off"></label><a class="web-button" href="/api/v1/staff/overview/stats/export/excel" download>Скачать Excel</a></div><div class="web-grid web-kpi-grid"><article class="web-card"><span class="web-kicker">Всего атлетов</span><div class="web-value">${m.total_athletes || all.length}</div></article><article class="web-card"><span class="web-kicker">Сейчас в зале</span><div class="web-value">${(m.active_sessions || []).length}</div></article><article class="web-card"><span class="web-kicker">Заканчиваются</span><div class="web-value">${(m.burning_students || []).length}</div></article></div><div data-search-results>${list('Все атлеты', all, 'В клубе пока нет атлетов.')}</div><div class="table-sections">${list('Сейчас в зале', m.active_sessions || [], 'Сейчас в зале никого нет.')}${list('Последние посещения', m.past_sessions || [], 'История посещений пуста.')}${list('Заканчиваются занятия', m.burning_students || [], 'Таких атлетов нет.')}${list('Спящие клиенты', m.sleeping_students || [], 'Спящих клиентов нет.')}</div></section>`;
    const search = root.querySelector('[data-search]');
    search.oninput = () => { const q = search.value.trim().toLocaleLowerCase(); root.querySelector('[data-search-results]').innerHTML = list(`Результаты поиска${q ? `: ${esc(search.value)}` : ''}`, all.filter(row => String(row.name || '').toLocaleLowerCase().includes(q)), 'Атлеты не найдены.'); };
  };
  root.innerHTML = '<p class="web-status">Загрузка таблицы…</p>';
  api('/api/v1/staff/overview/data').then(render).catch(error => { root.innerHTML = `<p class="web-error" role="alert">Не удалось загрузить таблицу: ${esc(error.message)}</p>`; });
})();
