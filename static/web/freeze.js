(() => {
  const root = document.querySelector('#freeze');
  if (!root) return;
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const csrf = () => decodeURIComponent((document.cookie.match(/(?:^|; )speedycrm_csrf_token=([^;]*)/) || [])[1] || '');
  const request = async (url, options = {}) => {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail?.message || data.detail?.code || 'Операция недоступна');
    return data;
  };
  root.innerHTML = `<section class="web-card"><h2>Новая заморозка</h2>
    <form data-apply><label>Атлет<select name="student_id" required><option value="">Загрузка…</option></select></label>
    <label>Заморозить до<input name="date_to" type="date" required></label>
    <button type="submit">Заморозить</button><span data-result role="status"></span></form></section>
    <section class="web-card"><h2>Текущие заморозки</h2><div data-list>Загрузка…</div></section>`;
  const form = root.querySelector('[data-apply]');
  const select = form.querySelector('[name=student_id]');
  const list = root.querySelector('[data-list]');
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const iso = d => new Date(d).toISOString().slice(0, 10);
  const defaultDate = new Date(today); defaultDate.setDate(defaultDate.getDate() + 7); form.date_to.value = iso(defaultDate);
  const load = async () => {
    const [students, freezes] = await Promise.all([
      request('/api/v1/staff/students/data?limit=500&full=true'),
      request('/api/v1/staff/freeze/data')
    ]);
    select.innerHTML = '<option value="">Выберите атлета</option>' + (students.students || []).filter(s => !s.is_frozen && s.has_subscription).map(s => `<option value="${s.id}">${esc(s.name)} · ${esc(s.discipline)}</option>`).join('');
    const rows = freezes.frozen || [];
    list.innerHTML = rows.length ? `<div class="web-table-wrap"><table class="web-table"><thead><tr><th>Атлет</th><th>Начало</th><th>До</th><th>Срок</th><th></th></tr></thead><tbody>${rows.map(s => `<tr><td>❄️ ${esc(s.name)}</td><td>${esc(s.frozen_at)}</td><td>${esc(s.frozen_until)}</td><td>${esc(s.frozen_days)} дн.</td><td><button type="button" data-unfreeze="${s.id}">Разморозить</button></td></tr>`).join('')}</tbody></table></div>` : '<p class="web-empty">Сейчас замороженных атлетов нет.</p>';
    list.querySelectorAll('[data-unfreeze]').forEach(button => button.onclick = async () => {
      if (!confirm('Разморозить атлета?')) return;
      button.disabled = true;
      try { await request('/api/v1/staff/freeze/unfreeze', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()}, body:JSON.stringify({student_id:Number(button.dataset.unfreeze), idempotency_key:crypto.randomUUID()})}); await load(); }
      catch (error) { button.disabled = false; alert(error.message); }
    });
  };
  form.onsubmit = async event => {
    event.preventDefault();
    const result = form.querySelector('[data-result]');
    const dateTo = form.date_to.value;
    const days = Math.round((new Date(`${dateTo}T00:00:00`) - today) / 86400000);
    if (days < 1 || days > 365) { result.textContent = 'Дата должна быть от 1 до 365 дней от сегодня.'; return; }
    try { await request('/api/v1/staff/freeze/apply', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()}, body:JSON.stringify({student_id:Number(select.value), date_to:dateTo, idempotency_key:crypto.randomUUID()})}); result.textContent = 'Заморозка применена.'; await load(); }
    catch (error) { result.textContent = error.message; }
  };
  load().catch(error => { list.textContent = error.message; select.innerHTML = '<option value="">Не удалось загрузить атлетов</option>'; });
})();
