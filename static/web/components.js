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
    if (sales) sales.appendChild(form('<h2>Cash product sale</h2><form><input name="product_id" type="number" min="1" placeholder="Product ID" required><input name="quantity" type="number" min="1" max="99" value="1" required><input name="buyer_user_id" type="number" min="1" placeholder="Buyer user ID" required><button>Sell</button></form><p data-operation-result role="status"></p>', async event => { event.preventDefault(); const f = new FormData(event.target), result = event.currentTarget.querySelector("[data-operation-result]"); try { const data = await post("/api/v1/staff/sales/cash-product", {buyer_user_id: Number(f.get("buyer_user_id")), items: [{product_id: Number(f.get("product_id")), quantity: Number(f.get("quantity"))}]}); result.textContent = `Sale ${data.order_id} saved.`; } catch (_) { result.textContent = "Sale unavailable or disabled."; } }));
  }, 0);
});
