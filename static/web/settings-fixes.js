(() => {
  const api = (url, options) => SpeedyCRMWeb.json(url, options);
  const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;", "'":"&#39;"}[c]));
  const csrf = () => decodeURIComponent((document.cookie.match(/(?:^|; )speedycrm_csrf_token=([^;]*)/) || [])[1] || "");
  const path = location.pathname;
  const host = document.querySelector("#settings");
  if (!host || !path.startsWith("/staff/settings/")) return;
  const request = (url, body) => api(url, {method:"PATCH", headers:{"Content-Type":"application/json", "X-CSRF-Token":csrf()}, body:JSON.stringify({...body, idempotency_key:crypto.randomUUID()})});
  if (path.endsWith("/legal")) {
    api("/api/v1/staff/settings/legal").then(data => { const legal = data.legal || {}; host.innerHTML = `<h2>Юридическая информация клуба</h2><dl class="web-definition-list">${Object.entries({provider_name:"Организация", provider_type:"Тип организации", inn:"ИНН", ogrn:"ОГРН", legal_address:"Юридический адрес", club_address:"Адрес клуба", email:"Email", phone:"Телефон", document_version:"Версия документов"}).map(([key,label]) => `<dt>${label}</dt><dd>${esc(legal[key] || "Не указано")}</dd>`).join("")}</dl>`; }).catch(() => { host.textContent = "Не удалось загрузить юридическую информацию."; });
  }
  if (path.endsWith("/work-schedule")) {
    api("/api/v1/staff/settings/work-schedule").then(data => { const days = {mon:"Понедельник", tue:"Вторник", wed:"Среда", thu:"Четверг", fri:"Пятница", sat:"Суббота", sun:"Воскресенье"}, value = data.work_schedule || {}; host.innerHTML = `<h2>График работы клуба</h2><form data-work-schedule>${Object.entries(days).map(([key,label]) => `<label>${label}<div class="web-form-grid"><input name="${key}_open" type="time" value="${esc(value[key]?.open || "09:00")}" aria-label="${label}: открытие"><input name="${key}_close" type="time" value="${esc(value[key]?.close || "21:00")}" aria-label="${label}: закрытие"></div></label>`).join("")}<button>Сохранить график</button><p data-result role="status"></p></form>`; host.querySelector("form").onsubmit = async e => { e.preventDefault(); const f = new FormData(e.target), schedule = {}; Object.keys(days).forEach(key => { schedule[key] = {open:f.get(`${key}_open`), close:f.get(`${key}_close`)}; }); try { await request("/api/v1/staff/settings/work-schedule", {work_schedule:schedule}); host.querySelector("[data-result]").textContent = "График сохранён."; } catch (_) { host.querySelector("[data-result]").textContent = "Не удалось сохранить график."; } }; }).catch(() => { host.textContent = "Не удалось загрузить график работы."; });
  }
})();
