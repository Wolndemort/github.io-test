(() => {
  const api = (url, options) => SpeedyCRMWeb.json(url, options);
  const esc = value => String(value ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
  const staff = location.pathname.startsWith("/staff/") || location.pathname === "/staff";
  const studentId = location.pathname.match(/^\/staff\/students\/(\d+)$/)?.[1];
  function addMobileNav() {
    if (document.querySelector("[data-mobile-nav]")) return;
    const links = staff ? [["/staff/overview", "Главная"], ["/staff/students", "Атлеты"], ["/staff/cash-sales", "Продажи"], ["/staff", "Ещё"]] : [["/client/cabinet", "Кабинет"], ["/client/schedule", "Расписание"], ["/client/products", "Магазин"], ["/client/me", "Профиль"]];
    const nav = document.createElement("nav"); nav.dataset.mobileNav = "1"; nav.className = "web-mobile-nav"; nav.setAttribute("aria-label", "Быстрая навигация");
    nav.innerHTML = links.map(([href, label]) => `<a href="${href}"${location.pathname === href ? ' aria-current="page"' : ""}>${label}</a>`).join(""); document.body.appendChild(nav);
  }
  function addSearch() {
    if (!staff || document.querySelector("[data-global-search]")) return; const nav = document.querySelector(".web-nav"); if (!nav) return;
    const box = document.createElement("div"); box.dataset.globalSearch = "1"; box.className = "web-global-search"; box.innerHTML = '<input type="search" placeholder="Поиск атлета или телефона" aria-label="Поиск атлета или телефона"><div class="web-search-results" hidden></div>'; nav.insertAdjacentElement("afterend", box);
    const input = box.querySelector("input"), results = box.querySelector(".web-search-results"); let timer;
    input.addEventListener("input", () => { clearTimeout(timer); const q = input.value.trim(); if (!q) { results.hidden = true; return; } timer = setTimeout(async () => { try { const data = await api(`/api/v1/staff/overview/search?q=${encodeURIComponent(q)}`); results.innerHTML = (data.results || []).map(item => `<a href="/staff/students/${item.id}"><strong>${esc(item.name)}</strong><small>${esc(item.discipline)}</small></a>`).join("") || '<span class="web-empty">Ничего не найдено</span>'; results.hidden = false; } catch (_) { results.hidden = true; } }, 180); });
  }
  function dashboard() {
    if (location.pathname !== "/staff/overview") return; const host = document.querySelector("#overview"); if (!host || host.dataset.uxDashboard) return; host.dataset.uxDashboard = "1";
    const render = async () => { try { const data = await api("/api/v1/staff/overview/data"), m = data.metrics || {}; const cards = [{label:"Атлеты", value:m.total_students ?? m.total_athletes ?? 0}, {label:"Активные абонементы", value:m.active_students ?? m.active_subscriptions ?? 0}, {label:"Сегодня в клубе", value:m.today_visits ?? m.visits_today ?? 0}]; host.innerHTML = `<div class="web-grid web-kpi-grid">${cards.map(c => `<article class="web-card"><span class="web-kicker">${c.label}</span><div class="web-value">${esc(c.value)}</div></article>`).join("")}</div><section class="web-card"><h2>Сейчас в клубе</h2>${(m.active_sessions || []).map(s => `<div class="web-live-row"><strong>${esc(s.name)}</strong><span>до ${esc(s.session_end)}</span></div>`).join("") || '<p class="web-empty">Сейчас никого нет.</p>'}</section>`; } catch (_) {} };
    render(); setInterval(render, 30000);
  }
  function athleteDetail() {
    if (!studentId) return; const host = document.querySelector("#student"); if (!host || host.dataset.uxDetail) return; host.dataset.uxDetail = "1";
    api(`/api/v1/staff/students/${studentId}`).then(async data => { const [visits, payments, discounts] = await Promise.all([api(`/api/v1/staff/students/${studentId}/visits`), api(`/api/v1/staff/students/${studentId}/payments`), api(`/api/v1/staff/students/${studentId}/discounts`)]), s = data.student || {}; host.innerHTML = `<div class="athlete-title"><h2>${esc(s.name)}</h2><span>${esc(s.discipline)}</span></div><p>${s.is_frozen ? "❄️ Заморожен" : s.expire_date ? `Абонемент до ${esc(s.expire_date)}` : "Без активного абонемента"} · занятий: ${esc(s.balance_lessons)}</p><div class="web-tabs"><details open><summary>Посещения (${visits.visits?.length || 0})</summary>${SpeedyCRMWeb.table([{key:"visited_at",label:"Дата"},{key:"source",label:"Источник"}], (visits.visits || []).map(v => ({visited_at:esc(v.visited_at), source:esc(v.source)})), "Посещений нет")}</details><details><summary>Оплаты (${payments.payments?.length || 0})</summary>${SpeedyCRMWeb.table([{key:"created_at",label:"Дата"},{key:"amount",label:"Сумма"},{key:"type",label:"Тип"}], (payments.payments || []).map(p => ({created_at:esc(p.created_at), amount:`${(Number(p.amount_kopecks || 0) / 100).toFixed(2)} ₽`, type:esc(p.type)})), "Оплат нет")}</details><details><summary>Скидки (${discounts.discounts?.length || 0})</summary>${SpeedyCRMWeb.table([{key:"name",label:"Название"},{key:"scope",label:"Область"}], (discounts.discounts || []).map(d => ({name:esc(d.name), scope:esc(d.scope)})), "Скидок нет")}</details></div>`; }).catch(() => {});
  }
  function saveFilters() { if (location.pathname !== "/staff/students") return; const form = document.querySelector("#students form"); if (!form || form.dataset.savedFilter) return; form.dataset.savedFilter = "1"; const key = "speedycrm_students_filter", saved = localStorage.getItem(key); if (saved) Object.entries(JSON.parse(saved)).forEach(([name, value]) => { const field = form.elements[name]; if (field) field.value = value; }); form.addEventListener("submit", () => localStorage.setItem(key, JSON.stringify(Object.fromEntries(new FormData(form))))); }
  document.addEventListener("DOMContentLoaded", () => { addMobileNav(); addSearch(); dashboard(); athleteDetail(); saveFilters(); });
})();
