window.SpeedyCRMWeb = {
  navigation(label) {
    const client = String(label || "").toLowerCase().includes("client");
    const language = localStorage.getItem("speedycrm_language") || "en";
    document.documentElement.lang = language;
    const links = client ? `<a href="/client/cabinet">Cabinet</a><a href="/client/subscriptions">Subscriptions</a><a href="/client/purchases">Purchases</a><a href="/client/history">History</a><a href="/client/freeze">Freeze</a><a href="/client/schedule">Schedule</a><a href="/client/products">Products</a><a href="/client/discounts">Discounts</a><a href="/client/tariffs">Tariffs</a><a href="/client/club">Club</a><a href="/client/me">Profile</a><a href="/client/legal">Legal</a><a href="/client/summary/attendance">Attendance</a><a href="/client/summary/subscriptions">Summary</a><a href="/client/summary/purchases">Purchase summary</a>` : `<a href="/staff/overview">Overview</a><a href="/staff/forecast">Forecast</a><a href="/staff/revenue">Revenue</a><a href="/staff/students">Students</a><a href="/staff/cash">Cash</a><a href="/staff/sales">Sales</a><a href="/staff/audit">Audit</a><a href="/staff/schedule">Schedule</a><a href="/staff/products">Products</a><a href="/staff/discounts">Discounts</a><a href="/staff/tariffs">Tariffs</a><a href="/staff/checkin">Check-in</a><a href="/staff/freeze">Freeze</a><a href="/staff/settings/legal">Legal settings</a><a href="/staff/settings/camera">Camera</a><a href="/staff/settings/features">Features</a><a href="/staff/settings/limits">Limits</a><a href="/staff/settings/branding">Branding</a><a href="/staff/settings/integrations">Integrations</a>`;
    return `<nav class="web-nav"><a class="web-brand" href="${client ? "/client/cabinet" : "/staff/overview"}">SpeedyCRM</a><span class="web-kicker">${label || "Staff web"}</span><div class="web-links">${links}<a href="/auth/email-profile">Email login</a><label class="web-language">Language <select data-web-language onchange="SpeedyCRMWeb.setLanguage(this.value)"><option value="en" ${language === "en" ? "selected" : ""}>EN</option><option value="ru" ${language === "ru" ? "selected" : ""}>RU</option></select></label><button type="button" data-web-logout onclick="SpeedyCRMWeb.logout()">Logout</button></div></nav>`;
  },
  loading(message = "Загрузка данных…") {
    return `<p class="web-card web-status" data-web-loading>${message}</p>`;
  },
  error(message = "Не удалось загрузить данные") {
    return `<p class="web-card web-error" role="alert">${message}</p>`;
  },
  replaceWithError(target, message) {
    target.innerHTML = this.error(message);
  },
  breadcrumb(items) {
    return `<div class="web-breadcrumb">${(items || []).map(item => `<a href="${item.href || "#"}">${item.label}</a>`).join(" / ")}</div>`;
  },
  setLanguage(language) {
    if (!["en", "ru"].includes(language)) return;
    localStorage.setItem("speedycrm_language", language);
    document.documentElement.lang = language;
  },
  bindLanguage() {
    const language = localStorage.getItem("speedycrm_language") || "en";
    document.documentElement.lang = language;
    const select = document.querySelector("[data-web-language]");
    if (select) select.value = language;
  },
  table(columns, rows, empty = "Нет данных") {
    if (!rows || !rows.length) return `<div class="web-empty">${empty}</div>`;
    return `<div class="web-table-wrap"><table class="web-table"><thead><tr>${columns.map(column => `<th>${column.label}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${columns.map(column => `<td>${row[column.key] ?? "—"}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  },
  async json(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  },
  bindLogout() {
    const button = document.querySelector("[data-web-logout]");
    if (!button) return;
    button.addEventListener("click", async () => {
      const match = document.cookie.match(/(?:^|; )speedycrm_csrf_token=([^;]*)/);
      const token = match ? decodeURIComponent(match[1]) : "";
      await this.json("/auth/logout", {method: "POST", headers: {"X-CSRF-Token": token}});
      window.location.href = "/auth/login";
    });
  },
  async logout() {
    const match = document.cookie.match(/(?:^|; )speedycrm_csrf_token=([^;]*)/);
    const token = match ? decodeURIComponent(match[1]) : "";
    await this.json("/auth/logout", {method: "POST", headers: {"X-CSRF-Token": token}});
    window.location.href = "/auth/login";
  },
  async mountEmailBinding(targetId) {
    const target = document.getElementById(targetId);
    if (!target) return;
    const me = await this.json("/auth/me");
    if (me.email) {
      target.innerHTML = `<h2>Email login</h2><p>Verified: ${me.email}</p><p>Passwordless Web login is enabled for this account.</p>`;
      return;
    }
    target.innerHTML = `<h2>Email login</h2><p>Add an email to enable passwordless Web login.</p><form data-email-bind><input type="email" name="email" required placeholder="you@example.com"><button>Send code</button></form><div data-email-result role="status"></div>`;
    const csrf = () => decodeURIComponent((document.cookie.match(/(?:^|; )speedycrm_csrf_token=([^;]*)/) || [])[1] || "");
    const form = target.querySelector("[data-email-bind]");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const email = new FormData(form).get("email");
      const result = target.querySelector("[data-email-result]");
      try {
        await this.json("/auth/native/email/request", {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf()}, body: JSON.stringify({email, club_id: me.club_id})});
        const code = window.prompt("Enter the code from your email");
        if (!code) return;
        const verified = await this.json("/auth/native/email/verify", {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf()}, body: JSON.stringify({email, club_id: me.club_id, code})});
        result.textContent = `Email verified: ${verified.email}`;
      } catch (error) { result.textContent = "Email verification is unavailable."; }
    });
  }
};

// Functional operation panels are mounted consistently on the existing pages.
// Server-side feature flags remain the final safety gate.
document.addEventListener("DOMContentLoaded", () => {
  const csrf = () => decodeURIComponent((document.cookie.match(/(?:^|; )speedycrm_csrf_token=([^;]*)/) || [])[1] || "");
  const post = (url, body) => SpeedyCRMWeb.json(url, {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf()}, body: JSON.stringify({...body, idempotency_key: crypto.randomUUID()})});
  const form = (html, handler) => { const wrapper = document.createElement("div"); wrapper.className = "web-card"; wrapper.innerHTML = html; wrapper.querySelector("form").addEventListener("submit", handler); return wrapper; };
  setTimeout(() => {
    const cash = document.querySelector("#cash");
    if (cash) cash.appendChild(form('<h2>Cash operation</h2><form><select name="entry_type"><option value="income">Income</option><option value="expense">Expense</option></select><input name="amount" type="number" min="0.01" step="0.01" placeholder="Amount" required><input name="description" maxlength="500" placeholder="Reason" required><button>Save</button></form><p data-operation-result role="status"></p>', async event => { event.preventDefault(); const f = new FormData(event.target), result = event.currentTarget.querySelector("[data-operation-result]"); try { await post("/api/v1/staff/cash/entries", {entry_type: f.get("entry_type"), amount_kopecks: Math.round(Number(f.get("amount")) * 100), description: f.get("description"), category: "other"}); result.textContent = "Cash entry saved."; } catch (_) { result.textContent = "Operation unavailable or disabled."; } }));
    const sales = document.querySelector("#sales");
    if (sales) { const salePanel = form('<h2>Cash product sale</h2><form><label>Product <select name="product_id" required><option value="">Loading products…</option></select></label><input name="quantity" type="number" min="1" max="99" value="1" required><label>Buyer <select name="buyer_user_id" required><option value="">Loading buyers…</option></select></label><label>Discounts <select name="discount_ids" multiple size="3"><option value="">Loading discounts…</option></select></label><button>Sell</button></form><p data-operation-result role="status"></p>', async event => { event.preventDefault(); const f = new FormData(event.target), result = event.currentTarget.querySelector("[data-operation-result]"); try { const data = await post("/api/v1/staff/sales/cash-product", {buyer_user_id: Number(f.get("buyer_user_id")), items: [{product_id: Number(f.get("product_id")), quantity: Number(f.get("quantity")), discount_ids: [...event.target.querySelectorAll('[name="discount_ids"] option:checked')].map(option => Number(option.value))}]}); result.textContent = `Sale ${data.order_id} saved.`; } catch (_) { result.textContent = "Sale unavailable or disabled."; } }); sales.appendChild(salePanel); Promise.all([SpeedyCRMWeb.json("/api/v1/staff/catalog/products"), SpeedyCRMWeb.json("/api/v1/staff/sales/buyers"), SpeedyCRMWeb.json("/api/v1/staff/catalog/discounts")]).then(([products, buyers, discounts]) => { salePanel.querySelector('[name="product_id"]').innerHTML = products.products.filter(item => item.stock > 0).map(item => `<option value="${item.id}">${item.name} · ${item.price_kopecks / 100} · ${item.stock} in stock</option>`).join("") || '<option value="">No stock</option>'; salePanel.querySelector('[name="buyer_user_id"]').innerHTML = buyers.buyers.map(item => `<option value="${item.id}">${item.name} · ${item.id}</option>`).join("") || '<option value="">No buyers</option>'; salePanel.querySelector('[name="discount_ids"]').innerHTML = discounts.discounts.filter(item => item.scope === "products" || item.scope === "all").map(item => `<option value="${item.id}">${item.name}</option>`).join("") || '<option value="">No discounts</option>'; }).catch(() => { salePanel.querySelector('[data-operation-result]').textContent = "Product sale selectors unavailable."; }); }
    if (location.pathname === "/staff/sales" && sales) { const panel = form('<h2>Cash subscription sale</h2><form><label>Student <select name="student_id" required><option value="">Loading students…</option></select></label><label>Tariff <select name="tariff" required><option value="">Loading tariffs…</option></select></label><button>Activate</button></form><p data-operation-result role="status"></p>', async event => { event.preventDefault(); const f = new FormData(event.target), selected = event.target.querySelector('[name="tariff"] option:checked'), result = event.currentTarget.querySelector("[data-operation-result]"); try { const data = await post("/api/v1/staff/sales/cash-subscription", {student_id: Number(f.get("student_id")), discipline: selected.dataset.discipline, tariff_idx: Number(selected.dataset.index), discount_ids: []}); result.textContent = `Subscription ${data.order_id} saved.`; } catch (_) { result.textContent = "Subscription unavailable or disabled."; } }); sales.appendChild(panel); SpeedyCRMWeb.json("/api/v1/staff/sales/options").then(data => { const students = panel.querySelector('[name="student_id"]'); students.innerHTML = data.students.map(s => `<option value="${s.id}">${s.name} · ${s.discipline || "—"}</option>`).join("") || '<option value="">No students</option>'; const tariffs = Object.entries(data.tariffs).flatMap(([discipline, list]) => list.map((tariff, index) => `<option data-discipline="${discipline}" data-index="${index}" value="${discipline}:${index}">${discipline} · ${tariff.name} · ${tariff.price}</option>`)); panel.querySelector('[name="tariff"]').innerHTML = tariffs.join("") || '<option value="">No tariffs</option>'; }).catch(() => { panel.querySelector('[data-operation-result]').textContent = "Subscription selectors unavailable."; }); }
    if (location.pathname === "/staff/checkin") {
      const target = document.querySelector("#checkin") || document.querySelector("main .web-container");
      if (target) target.appendChild(form('<h2>Manual check-in</h2><form><input name="student_id" type="number" min="1" placeholder="Student ID" required><label><input name="open_turnstile" type="checkbox"> Open turnstile</label><button>Check in</button></form><p data-operation-result role="status"></p><h2>Cancel visit</h2><form data-cancel-visit><input name="visit_id" type="number" min="1" placeholder="Visit ID" required><input name="reason" minlength="3" maxlength="500" placeholder="Cancellation reason" required><button>Cancel</button></form><p data-cancel-result role="status"></p>', async event => { event.preventDefault(); const f = new FormData(event.target), result = event.currentTarget.querySelector("[data-operation-result]"); try { const data = await post("/api/v1/staff/checkin/manual", {student_id: Number(f.get("student_id")), open_turnstile: f.get("open_turnstile") === "on"}); result.textContent = data.result?.message || "Check-in saved."; } catch (_) { result.textContent = "Check-in unavailable or disabled."; } }));
      const cancel = target.querySelector("[data-cancel-visit]"); cancel?.addEventListener("submit", async event => { event.preventDefault(); const f = new FormData(event.target), result = target.querySelector("[data-cancel-result]"); try { await post("/api/v1/staff/checkin/cancel", {visit_id: Number(f.get("visit_id")), reason: f.get("reason")}); result.textContent = "Visit cancelled."; } catch (_) { result.textContent = "Cancellation unavailable or disabled."; } });
    }
    if (location.pathname === "/client/freeze") {
      const target = document.querySelector("#client-data");
      if (target) { const panel = form('<h2>Purchase freeze</h2><form><label>Student <select name="student_id" required><option value="">Loading students…</option></select></label><input name="days" type="number" min="1" max="365" placeholder="Days" required><button>Freeze</button></form><p data-operation-result role="status"></p>', async event => { event.preventDefault(); const f = new FormData(event.target), result = event.currentTarget.querySelector("[data-operation-result]"); try { const data = await post("/api/v1/client/freeze/purchase", {student_id: Number(f.get("student_id")), days: Number(f.get("days")), discount_ids: []}); result.textContent = `Freeze ${data.order_id} saved.`; } catch (_) { result.textContent = "Freeze unavailable or disabled."; } }); target.parentElement.appendChild(panel); SpeedyCRMWeb.json("/api/v1/client/cabinet/data").then(data => { panel.querySelector('[name="student_id"]').innerHTML = data.students.map(s => `<option value="${s.id}">${s.name} · ${s.discipline || "—"}</option>`).join("") || '<option value="">No students</option>'; }).catch(() => { panel.querySelector('[data-operation-result]').textContent = "Freeze selectors unavailable."; }); }
    }
    if (location.pathname === "/client/purchases") {
      const target = document.querySelector("#client-data");
      if (target) target.parentElement.appendChild(form('<h2>Pay online</h2><form><input name="order_id" placeholder="Pending order ID" required><button>Open payment</button></form><p data-operation-result role="status"></p>', async event => { event.preventDefault(); const f = new FormData(event.target), result = event.currentTarget.querySelector("[data-operation-result]"); try { const data = await post(`/api/v1/client/payments/${encodeURIComponent(f.get("order_id"))}/intent`, {}); result.innerHTML = `<a href="${data.payment_url}" target="_blank" rel="noopener">Continue to payment</a>`; } catch (_) { result.textContent = "Payment unavailable or order is not payable."; } }));
    }
    const settings = document.querySelector("#settings");
    if (settings && location.pathname.startsWith("/staff/settings/")) {
      const section = location.pathname.split("/").pop();
      const fields = {branding: [["club_name","Club name"],["logo_url","Logo URL"],["theme","Theme"]], limits: [["session_timeout_minutes","Session timeout"],["freeze_price_per_day","Freeze price/day"],["max_upload_mb","Max upload MB"],["max_students","Max students"]], features: [["freeze","Freeze enabled"],["qr_checkin","QR check-in enabled"],["online_payments","Online payments enabled"]], integrations: [["email_enabled","Email enabled"],["push_enabled","Push enabled"]], menu: [["show_schedule","Show schedule"],["show_shop","Show shop"],["show_payments","Show payments"]]}[section];
      if (fields) { const endpoint = section === "integrations" ? "/api/v1/staff/settings/integrations" : "/api/v1/staff/settings/club"; const body = section === "integrations" ? fields : fields; settings.parentElement.appendChild(form(`<h2>Update ${section}</h2><form>${fields.map(([key,label]) => `<label>${label}<input name="${key}" ${["freeze","qr_checkin","online_payments","email_enabled","push_enabled","show_schedule","show_shop","show_payments"].includes(key) ? "type=checkbox" : ""}></label>`).join("")}<button>Save settings</button></form><p data-operation-result role="status"></p>`, async event => { event.preventDefault(); const f = new FormData(event.target), data = {}; fields.forEach(([key]) => { data[key] = ["freeze","qr_checkin","online_payments","email_enabled","push_enabled","show_schedule","show_shop","show_payments"].includes(key) ? f.get(key) === "on" : f.get(key); }); const result = event.currentTarget.querySelector("[data-operation-result]"); try { await post(endpoint, section === "integrations" ? data : {[section === "branding" ? "branding" : section]: data}); result.textContent = "Settings saved."; } catch (_) { result.textContent = "Settings unavailable or disabled."; } })); }
    }
    if (location.pathname === "/staff/settings/staff") {
      const target = document.querySelector("#staff-list");
      if (target) target.parentElement.appendChild(form('<h2>Update permissions</h2><form><input name="staff_id" type="number" min="1" placeholder="Staff ID" required><input name="permission" placeholder="Permission name" required><select name="mode"><option value="allow">Allow</option><option value="deny">Deny</option></select><button>Save permission</button></form><p data-operation-result role="status"></p>', async event => { event.preventDefault(); const f = new FormData(event.target), result = event.currentTarget.querySelector("[data-operation-result]"); try { const data = await SpeedyCRMWeb.json(`/api/v1/staff/settings/staff/${Number(f.get("staff_id"))}`, {method: "PATCH", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf()}, body: JSON.stringify({permissions: {[f.get("mode")]: [String(f.get("permission"))]}, idempotency_key: crypto.randomUUID()})}); result.textContent = `Staff ${data.staff_id} permissions saved.`; } catch (_) { result.textContent = "Permission update unavailable or disabled."; } }));
      if (target) target.parentElement.appendChild(form('<h2>Edit staff</h2><form><input name="staff_id" type="number" min="1" placeholder="Staff ID" required><input name="full_name" placeholder="Full name"><select name="role"><option value="cashier">Cashier</option><option value="coach">Coach</option><option value="manager">Manager</option></select><label><input name="is_active" type="checkbox" checked> Active</label><button>Save staff</button></form><p data-operation-result role="status"></p>', async event => { event.preventDefault(); const f = new FormData(event.target), result = event.currentTarget.querySelector("[data-operation-result]"); try { const data = await SpeedyCRMWeb.json(`/api/v1/staff/settings/staff/${Number(f.get("staff_id"))}`, {method: "PATCH", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf()}, body: JSON.stringify({full_name: f.get("full_name"), role: f.get("role"), is_active: f.get("is_active") === "on", idempotency_key: crypto.randomUUID()})}); result.textContent = `Staff ${data.staff_id} saved.`; } catch (_) { result.textContent = "Staff update unavailable or disabled."; } }));
    }
    if (location.pathname === "/client/me") {
      const target = document.querySelector("#profile");
      if (target) target.parentElement.appendChild(form('<h2>Edit profile</h2><form><input name="full_name" minlength="2" maxlength="150" placeholder="Full name" required><button>Save profile</button></form><p data-operation-result role="status"></p>', async event => { event.preventDefault(); const f = new FormData(event.target), result = event.currentTarget.querySelector("[data-operation-result]"); try { await post("/api/v1/client/me", {full_name: f.get("full_name")}); result.textContent = "Profile saved."; } catch (_) { result.textContent = "Profile editing unavailable or disabled."; } }));
    }
  }, 0);
});
