(function () {
  if (document.getElementById('scrollTopButton')) return;
  const button = document.createElement('button');
  button.id = 'scrollTopButton';
  button.type = 'button';
  button.textContent = '↑';
  button.title = 'Наверх';
  button.style.cssText = 'position:fixed;right:16px;bottom:18px;z-index:98;width:44px;height:44px;border-radius:50%;background:#fff;color:#090909;font-size:20px;border:1px solid #444;cursor:pointer;box-shadow:0 10px 24px rgba(0,0,0,.18)';
  button.onclick = () => window.scrollTo({ top: 0, behavior: 'smooth' });
  document.body.appendChild(button);
})();
