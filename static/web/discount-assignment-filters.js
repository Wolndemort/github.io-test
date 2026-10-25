document.addEventListener('DOMContentLoaded', () => {
  const searchButton = document.querySelector('#searchClients');
  const queryInput = document.querySelector('#clientQuery');
  const clients = document.querySelector('#clients');
  if (!searchButton || !queryInput || !clients || typeof change !== 'function') return;
  const tg = window.Telegram?.WebApp;
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const filterBox = document.createElement('details');
  filterBox.innerHTML = `<summary>Фильтры аудитории</summary><div class="discount-filters"><label><input data-no-subscription type="checkbox"> Без действующего абонемента</label><label><input data-no-visits type="checkbox"> Не посещали 14+ дней</label><label>Дисциплина<select data-discipline><option value="">Все дисциплины</option></select></label></div>`;
  queryInput.parentElement.insertBefore(filterBox, queryInput);
  const noSubscription = filterBox.querySelector('[data-no-subscription]');
  const noVisits = filterBox.querySelector('[data-no-visits]');
  const discipline = filterBox.querySelector('[data-discipline]');
  const render = data => {
    const rows = (data.clients || []).flatMap(client => (client.student_rows || []).map(student => ({client, student})));
    clients.innerHTML = rows.length ? `<p class="muted">Подходит атлетов: <b>${rows.length}</b></p>` + rows.map(({client, student}) => { const assigned = student.discount_ids?.includes(Number(document.querySelector('#assignmentDiscount').value)); return `<div class="client"><div><b>${escapeHtml(client.name)}</b><br><span class="muted">${escapeHtml(student.name)} · ${escapeHtml(student.discipline || 'Без дисциплины')}${assigned ? ' · скидка уже назначена' : ''}</span></div>${assigned ? '<button type="button" disabled>Уже назначена</button>' : `<button type="button" data-assign-student="${student.id}">Привязать</button>`}</div>`; }).join('') : '<p class="muted">Ничего не найдено</p>';
    clients.querySelectorAll('[data-assign-student]').forEach(button => button.onclick = () => change('assign', Number(document.querySelector('#assignmentDiscount').value), null, null, Number(button.dataset.assignStudent)));
  };
  const load = async () => {
    const params = new URLSearchParams({club_id: String(cid), q: queryInput.value, init_data: tg?.initData || '', discipline: discipline.value, no_subscription: String(noSubscription.checked), no_visits_14: String(noVisits.checked)});
    try {
      const response = await fetch(`/webapp/admin-discounts/clients?${params}`); const data = await response.json();
      const current = discipline.value; discipline.innerHTML = '<option value="">Все дисциплины</option>' + (data.disciplines || []).map(item => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.name)}</option>`).join(''); discipline.value = current; render(data);
    } catch (_) { clients.textContent = 'Не удалось загрузить аудиторию.'; }
  };
  searchButton.onclick = load;
  [noSubscription, noVisits, discipline].forEach(input => input.onchange = load);
  queryInput.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); load(); } };
  const style = document.createElement('style'); style.textContent = '.discount-filters{display:grid;gap:8px;padding:12px 0}.discount-filters label{display:flex;gap:8px;align-items:center}.discount-filters input[type=checkbox]{width:auto}.discount-filters select{margin:0}'; document.head.appendChild(style);
});
