window.SpeedyCRMWeb = {
  navigation(label) {
    const client = String(label || "").toLowerCase().includes("client");
    const links = client ? `<a href="/client/cabinet">Cabinet</a><a href="/client/subscriptions">Subscriptions</a><a href="/client/purchases">Purchases</a><a href="/client/history">History</a><a href="/client/freeze">Freeze</a><a href="/client/schedule">Schedule</a><a href="/client/products">Products</a><a href="/client/discounts">Discounts</a><a href="/client/tariffs">Tariffs</a><a href="/client/club">Club</a><a href="/client/me">Profile</a><a href="/client/legal">Legal</a><a href="/client/summary/attendance">Attendance</a><a href="/client/summary/subscriptions">Summary</a><a href="/client/summary/purchases">Purchase summary</a>` : `<a href="/staff/overview">Overview</a><a href="/staff/forecast">Forecast</a><a href="/staff/revenue">Revenue</a><a href="/staff/students">Students</a><a href="/staff/cash">Cash</a><a href="/staff/sales">Sales</a><a href="/staff/audit">Audit</a><a href="/staff/schedule">Schedule</a><a href="/staff/products">Products</a><a href="/staff/discounts">Discounts</a><a href="/staff/tariffs">Tariffs</a><a href="/staff/checkin">Check-in</a><a href="/staff/freeze">Freeze</a><a href="/staff/settings/legal">Legal settings</a><a href="/staff/settings/camera">Camera</a><a href="/staff/settings/features">Features</a><a href="/staff/settings/limits">Limits</a><a href="/staff/settings/branding">Branding</a><a href="/staff/settings/integrations">Integrations</a>`;
    return `<nav class="web-nav"><a class="web-brand" href="${client ? "/client/cabinet" : "/staff/overview"}">SpeedyCRM</a><span class="web-kicker">${label || "Staff web"}</span><div class="web-links">${links}<button type="button" data-web-logout onclick="SpeedyCRMWeb.logout()">Logout</button></div></nav>`;
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
  }
};
