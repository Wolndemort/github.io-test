(function () {
  if (document.getElementById('fullscreen')) return;
  const button = document.createElement('button');
  button.id = 'fullscreen';
  button.textContent = '⛶';
  button.title = 'На весь экран';
  button.style.cssText = 'position:fixed;right:16px;top:16px;z-index:99;width:44px;height:44px;border-radius:10px;background:#fff;color:#090909;font-size:22px;border:1px solid #444;cursor:pointer';
  button.onclick = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (document.documentElement.requestFullscreen) await document.documentElement.requestFullscreen();
      else window.Telegram?.WebApp?.expand();
    } catch (_) { window.Telegram?.WebApp?.expand(); }
  };
  document.body.appendChild(button);
})();
