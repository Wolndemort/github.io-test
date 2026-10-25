(() => {
  const api = (url, options) => SpeedyCRMWeb.json(url, options);
  const esc = value => String(value ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;", "'":"&#39;"}[c]));
  const target = selector => document.querySelector(selector);

  if (location.pathname === "/client/cabinet") {
    const host = target("#client-data");
    if (host) api("/api/v1/client/cabinet/data").then(data => {
      host.innerHTML = `<h2>Мои атлеты</h2><div class="web-grid">${(data.students || []).map(s => `<article class="web-card"><h3>${esc(s.name)}</h3><p>${esc(s.discipline || "—")}</p><p>${s.is_frozen ? "❄️ Заморожен" : s.expire_date ? `Абонемент до ${esc(s.expire_date)}` : "Абонемент не активен"}</p><p>Занятий: ${esc(s.balance_lessons)}</p><a class="web-button" href="/client/subscriptions">Купить абонемент</a></article>`).join("") || '<p class="web-empty">Атлетов пока нет.</p>'}</div>`;
    }).catch(() => {});
  }

  if (location.pathname === "/client/me") {
    const host = target("#profile");
    if (host) api("/api/v1/client/me").then(data => {
      host.innerHTML = `<h2>${esc(data.full_name || "Профиль клиента")}</h2><dl class="web-definition-list"><dt>Email</dt><dd>${esc(data.email || "не привязан")}</dd><dt>Клуб</dt><dd>${esc(data.club_id)}</dd><dt>Источник входа</dt><dd>${esc(data.auth_source)}</dd></dl><p><a href="/auth/email-profile">Управление Web-доступом</a></p>`;
    }).catch(() => {});
  }

  if (location.pathname === "/client/pass") {
    const host = target("#client-data, #data");
    if (host) api("/api/v1/client/pass/data").then(data => {
      host.innerHTML = `<h2>QR-пропуск</h2><p class="web-status">Код обновляется каждый час.</p><div class="web-grid">${(data.passes || []).map(pass => `<article class="web-card"><h3>${esc(pass.name)}</h3><img class="web-qr" src="${pass.image_data_url}" alt="QR-пропуск: ${esc(pass.name)}"><p class="web-status">Действует до конца часа</p></article>`).join("") || '<p class="web-empty">Нет доступных атлетов.</p>'}</div>`;
    }).catch(() => {});
  }

  if (location.pathname === "/client/schedule") {
    const host = target("#schedule, #client-data, #data");
    if (host) api("/api/v1/client/schedule/data").then(data => {
      const days = {mon:"Пн", tue:"Вт", wed:"Ср", thu:"Чт", fri:"Пт", sat:"Сб", sun:"Вс"};
      const rows = [];
      Object.entries(data.schedule || {}).forEach(([discipline, week]) => Object.entries(week || {}).forEach(([day, lessons]) => (lessons || []).forEach(lesson => {
        const item = typeof lesson === "object" ? lesson : {time: lesson};
        rows.push({discipline, day: days[day] || day, time: item.time || item.start || "—", duration: item.duration ? `${item.duration} мин.` : "—", coach: item.coach || "—", group: item.group || "—"});
      })));
      host.innerHTML = `<h2>Расписание клуба</h2>${SpeedyCRMWeb.table([{key:"discipline",label:"Дисциплина"},{key:"day",label:"День"},{key:"time",label:"Время"},{key:"duration",label:"Длительность"},{key:"coach",label:"Тренер"},{key:"group",label:"Группа"}], rows.map(row => Object.fromEntries(Object.entries(row).map(([k,v]) => [k, esc(v)]))), "Расписание пока не заполнено.")}`;
    }).catch(() => {});
  }

  if (location.pathname === "/client/products") {
    const host = target("#products, #client-data");
    if (host) api("/api/v1/client/products/data").then(data => {
      host.innerHTML = `<h2>Магазин клуба</h2><div class="web-grid">${(data.products || []).map(p => `<article class="web-card product-card">${p.image_url ? `<img class="product-image" src="${esc(p.image_url)}" alt="${esc(p.name)}">` : '<div class="product-image product-image-empty">Нет изображения</div>'}<h3>${esc(p.name)}</h3><p>${esc(p.details || "")}</p><p><strong>${(Number(p.price_kopecks || 0) / 100).toFixed(2)} ₽</strong> · остаток: ${esc(p.stock)}</p><form data-product-order="${p.id}"><input name="quantity" type="number" min="1" max="${Math.max(1, Number(p.stock || 1))}" value="1" required><button>Добавить в покупки</button><span data-result role="status"></span></form></article>`).join("") || '<p class="web-empty">Товаров пока нет.</p>'}</div>`;
      host.querySelectorAll("[data-product-order]").forEach(form => form.onsubmit = async event => { event.preventDefault(); const quantity = Number(new FormData(form).get("quantity")), out = form.querySelector("[data-result]"); try { const result = await api("/api/v1/client/products/order", {method:"POST", headers:{"Content-Type":"application/json", "X-CSRF-Token":decodeURIComponent((document.cookie.match(/(?:^|; )speedycrm_csrf_token=([^;]*)/) || [])[1] || "")}, body:JSON.stringify({product_id:Number(form.dataset.productOrder), quantity, discount_ids:[], idempotency_key:crypto.randomUUID()})}); out.innerHTML = `Заказ создан: <a href="/client/purchases">перейти к оплате</a>`; } catch (_) { out.textContent = "Товар недоступен или продажа отключена."; } });
    }).catch(() => {});
  }
})();
