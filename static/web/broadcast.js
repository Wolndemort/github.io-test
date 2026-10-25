(() => {
  const form = document.querySelector('#broadcast');
  const card = form?.closest('.web-card');
  if (!card) return;
  const csrf = () => decodeURIComponent((document.cookie.match(/(?:^|; )speedycrm_csrf_token=([^;]*)/) || [])[1] || '');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const selected = new Set();
  let mode = 'selected';
  let students = [];
  let disciplines = [];
  const style = document.createElement('style');
  style.textContent = '.web-broadcast-list{display:grid;gap:8px;max-height:420px;overflow:auto;margin:12px 0}.web-broadcast-recipient{display:flex;align-items:center;gap:10px;padding:12px;border:1px solid #ddd;border-radius:12px;cursor:pointer}.web-broadcast-recipient input{width:20px;height:20px;flex:0 0 auto}.web-broadcast-recipient span{display:flex;flex-direction:column;gap:3px}.web-broadcast-recipient small{color:#777}.web-broadcast-recipient b{color:#111}';
  document.head.appendChild(style);

  card.innerHTML = `<h2>Рассылка в Telegram</h2>
    <p class="web-status">Найдите атлетов по имени или номеру родителя и выберите получателей.</p>
    <div class="web-form-grid"><input data-broadcast-search type="search" placeholder="Имя атлета или номер родителя"><button type="button" data-broadcast-search-button>Найти</button></div>
    <details data-broadcast-filters><summary>Фильтры</summary><div class="web-form-grid"><label><input data-filter-no-subscription type="checkbox"> Без действующего абонемента</label><label><input data-filter-no-visits type="checkbox"> Не посещали 14+ дней</label><label>Дисциплина<select data-filter-discipline><option value="">Все дисциплины</option></select></label><button type="button" data-select-visible>Выбрать всех найденных</button></div></details>
    <div data-broadcast-list class="web-broadcast-list">Загрузка списка…</div>
    <div class="web-form-grid"><button type="button" data-broadcast-selected>Рассылка выбранным</button><button type="button" data-broadcast-all>Рассылка всем</button></div>
    <section data-broadcast-compose hidden><label>Сообщение<textarea data-broadcast-text rows="7" maxlength="4096" placeholder="Введите текст рассылки" required></textarea></label><button type="button" data-broadcast-send>Отправить</button><div data-broadcast-progress hidden><progress></progress><span>Отправляем…</span></div><p data-broadcast-result role="status"></p></section>`;
  const search = card.querySelector('[data-broadcast-search]');
  const list = card.querySelector('[data-broadcast-list]');
  const compose = card.querySelector('[data-broadcast-compose]');
  const text = card.querySelector('[data-broadcast-text]');
  const result = card.querySelector('[data-broadcast-result]');
  const progress = card.querySelector('[data-broadcast-progress]');
  const noSubscription = card.querySelector('[data-filter-no-subscription]');
  const noVisits = card.querySelector('[data-filter-no-visits]');
  const discipline = card.querySelector('[data-filter-discipline]');
  const visibleStudents = () => students.filter(student => {
    const active = student.expire_date && student.expire_date >= new Date().toISOString().slice(0, 10) && Number(student.balance_lessons || 0) > 0;
    const lastVisit = student.last_visit ? new Date(student.last_visit).getTime() : 0;
    const stale = !lastVisit || Date.now() - lastVisit >= 14 * 24 * 60 * 60 * 1000;
    return (!noSubscription.checked || !active) && (!noVisits.checked || stale) && (!discipline.value || student.discipline === discipline.value);
  });
  const render = () => {
    const filtered = visibleStudents();
    list.innerHTML = filtered.length ? filtered.map(student => `<label class="web-broadcast-recipient"><input type="checkbox" data-student-id="${student.id}" ${selected.has(student.id) ? 'checked' : ''}><span><b>${esc(student.name)}</b><small>${esc(student.discipline || '')}${student.parent_phone ? ` · ${esc(student.parent_phone)}` : ''}${student.recipient_count ? ` · получателей: ${student.recipient_count}` : ' · нет привязанного получателя'}</small></span></label>`).join('') : '<p class="web-status">Атлеты не найдены.</p>';
    list.querySelectorAll('[data-student-id]').forEach(input => input.onchange = () => input.checked ? selected.add(Number(input.dataset.studentId)) : selected.delete(Number(input.dataset.studentId)));
  };
  const load = async () => { try { const query = encodeURIComponent(search.value.trim()), currentDiscipline = discipline.value; const data = await SpeedyCRMWeb.json(`/api/v1/staff/forecast/broadcast/recipients?q=${query}`); students = data.students || []; disciplines = data.disciplines || []; discipline.innerHTML = '<option value="">Все дисциплины</option>' + disciplines.map(item => `<option value="${esc(item.value)}">${esc(item.name)}</option>`).join(''); discipline.value = currentDiscipline; render(); } catch (_) { list.textContent = 'Не удалось загрузить список атлетов.'; } };
  const openCompose = nextMode => { mode = nextMode; if (mode === 'selected' && !selected.size) { result.textContent = 'Выберите хотя бы одного атлета.'; return; } compose.hidden = false; result.textContent = mode === 'all' ? 'Сообщение получат все пользователи клуба.' : `Выбрано атлетов: ${selected.size}.`; text.focus(); };
  card.querySelector('[data-broadcast-search-button]').onclick = load;
  search.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); load(); } };
  card.querySelector('[data-broadcast-selected]').onclick = () => openCompose('selected');
  card.querySelector('[data-broadcast-all]').onclick = () => openCompose('all');
  [noSubscription, noVisits, discipline].forEach(input => input.onchange = render);
  card.querySelector('[data-select-visible]').onclick = () => { visibleStudents().forEach(student => selected.add(student.id)); render(); };
  card.querySelector('[data-broadcast-send]').onclick = async () => {
    const message = text.value.trim();
    if (!message) { result.textContent = 'Введите текст сообщения.'; return; }
    progress.hidden = false; result.textContent = '';
    try {
      const payload = {text: message, idempotency_key: crypto.randomUUID()};
      if (mode === 'selected') payload.student_ids = [...selected];
      const response = await SpeedyCRMWeb.json('/api/v1/staff/forecast/broadcast', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()}, body:JSON.stringify(payload)});
      result.textContent = `Готово: доставлено ${response.delivered} из ${response.recipients}. Ошибок: ${response.failed}.`;
      text.value = '';
    } catch (_) { result.textContent = 'Рассылка недоступна или запрещена.'; }
    finally { progress.hidden = true; }
  };
  load();
})();
