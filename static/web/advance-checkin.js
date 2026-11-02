(() => {
  const boot = () => {
    const list = document.getElementById('list');
    const searchInput = document.getElementById('search');
    const status = document.getElementById('status');
    if (!list || !searchInput || !status || typeof window.showAlert !== 'function') return;
    const safe = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const init = window.Telegram?.WebApp?.initData || new URLSearchParams(location.search).get('init_data') || '';
    const clubId = new URLSearchParams(location.search).get('club_id') || '';
    const render = rows => {
      list.innerHTML = rows.map(s => {
        const a = s.advance || {};
        const warning = a.is_advance
          ? `<p class="advance-warning">⚠️ Абонемент закончился${s.expire_date ? `: ${safe(s.expire_date.slice(0, 10))}` : ''}. Следующее посещение пойдёт в долг: №${a.debt_lessons} из ${a.max_debt_lessons} занятий, ${a.debt_days} из ${a.max_debt_days} дней.</p>`
          : `<p class="advance-ok">✅ Абонемент действует до ${s.expire_date ? safe(s.expire_date.slice(0, 10)) : '—'}</p>`;
        const remaining = Number(s.remaining_lessons) === 999 ? 'безлимит' : `${Number(s.remaining_lessons ?? 0)} посещений`;
        const birthdayWarning = s.birthday_missing ? `<p class="birthday-warning">⚠️ Дата рождения не указана. Администратор может отметить проход, но её нужно заполнить в кабинете клиента.${s.can_edit_birthday ? ` <button type="button" class="birthday-edit-button" data-birthday-edit="${s.id}">Заполнить дату</button><span class="birthday-edit-form" data-birthday-form="${s.id}" hidden><input type="date" data-birthday-input="${s.id}"><button type="button" data-birthday-save="${s.id}">Сохранить</button><span data-birthday-result="${s.id}"></span></span>` : ''}</p>` : '';
        return `<div class="student"><b>${safe(s.name)}</b><br><small>${safe(s.phone)} · ${s.active ? 'Абонемент активен' : 'Абонемент закончился'}</small><p class="student-meta">🥋 Дисциплина: ${safe(s.discipline || '—')} · Остаток: ${safe(remaining)}</p>${birthdayWarning}${warning}<div class="actions"><button onclick="window.advanceCheck(${s.id},false)">Без турникета</button><button onclick="window.advanceCheck(${s.id},true)">С турникетом</button></div></div>`;
      }).join('') || '<p>Ничего не найдено</p>';
    };
    let latestRows = [];
    const renderSessionDetails = () => {
      list.querySelectorAll('.session-details').forEach(node => node.remove());
      list.querySelectorAll('.student').forEach((card, index) => {
        const row = latestRows[index];
        if (!row?.session_active) return;
        const until = new Date(row.session_until).getTime();
        const box = document.createElement('div');
        box.className = 'session-details';
        box.style = 'margin:8px 0;padding:9px;border:1px solid #16803c;border-radius:8px;background:#effaf1;color:#176b2c';
        box.innerHTML = `🟢 Сессия активна с ${new Date(row.session_started_at).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})} · до ${new Date(row.session_until).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})}<br><b data-session-countdown>Осталось —</b><br><button type="button" data-cancel-session="${row.id}" style="width:auto;padding:7px 10px;background:#fff;color:#8b1e1e;border:1px solid #c88;border-radius:7px;margin-top:6px">↩️ Отменить посещение</button>`;
        card.querySelector('.student-meta')?.after(box);
        const tick = () => { const left=Math.max(0, until-Date.now()), minutes=Math.floor(left/60000), seconds=Math.floor(left/1000)%60; const target=box.querySelector('[data-session-countdown]'); if(target) target.textContent=left?`Осталось ${minutes}:${String(seconds).padStart(2,'0')}`:'Сессия завершена'; if(left) setTimeout(tick,1000); };
        tick();
      });
    };
    window.advanceSearch = async () => {
      const q = searchInput.value;
      const response = await fetch(`/webapp/staff-checkin/search?club_id=${clubId}&q=${encodeURIComponent(q)}&init_data=${encodeURIComponent(init)}`);
      if (!response.ok) { status.textContent = 'Нет доступа'; return; }
      latestRows = await response.json();
      render(latestRows);
      renderSessionDetails();
    };
    window.advanceCheck = async (id, open, confirmed = false) => {
      status.textContent = 'Сохраняем…';
      const response = await fetch('/webapp/staff-checkin', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({club_id:clubId, student_id:id, open_turnstile:open, confirm_advance:confirmed, init_data:init})});
      const payload = await response.json().catch(() => ({}));
      if (response.ok) { showAlert(payload.already_marked ? 'Посещение уже отмечено' : 'Посещение успешно отмечено', `${payload.turnstile || ''}\n${payload.message || 'Сессия активна'}`, !!payload.already_marked); return; }
      const detail = payload.detail || {};
      if (detail.code === 'advance_confirmation_required' && window.confirm(`⚠️ ${detail.message}\n\nОтметить это посещение в долг?`)) return window.advanceCheck(id, open, true);
      showAlert('Не удалось отметить', detail.message || detail || 'Ошибка сохранения', true);
    };
    list.addEventListener('click', async event => {
      const edit = event.target.closest('[data-birthday-edit]');
      if (edit) { event.preventDefault(); const form = list.querySelector(`[data-birthday-form="${edit.dataset.birthdayEdit}"]`); if (form) { form.hidden = false; edit.hidden = true; form.querySelector('input')?.focus(); } return; }
      const save = event.target.closest('[data-birthday-save]');
      if (!save) return;
      const id = save.dataset.birthdaySave, input = list.querySelector(`[data-birthday-input="${id}"]`), result = list.querySelector(`[data-birthday-result="${id}"]`);
      if (!input?.value) { input?.focus(); return; }
      save.disabled = true; result.textContent = ' Сохраняем…';
      try {
        const response = await fetch('/webapp/client-cabinet/student/birthday', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({init_data:init, club_id:Number(clubId), student_id:Number(id), birthday:input.value})});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Не удалось сохранить дату');
        result.textContent = ' ✅ Дата сохранена';
        setTimeout(window.advanceSearch, 500);
      } catch (error) { result.textContent = ` ${error.message || 'Ошибка сохранения'}`; save.disabled = false; }
    });
    list.addEventListener('click', async event => {
      const button = event.target.closest('[data-cancel-session]');
      if (!button) return;
      event.preventDefault();
      if (!window.confirm('Отменить текущее посещение? Занятие не будет списано.')) return;
      button.disabled = true;
      try {
        const response = await fetch(`/webapp/admin/visits/${button.dataset.cancelSession}/cancel?club_id=${clubId}&init_data=${encodeURIComponent(init)}`, {method:'POST'});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Не удалось отменить посещение');
        await window.advanceSearch();
      } catch (error) { button.disabled = false; window.showAlert('Не удалось отменить посещение', error.message, true); }
    });
    searchInput.oninput = () => { clearTimeout(window.advanceSearchTimer); window.advanceSearchTimer = setTimeout(window.advanceSearch, 250); };
    window.advanceSearch();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
