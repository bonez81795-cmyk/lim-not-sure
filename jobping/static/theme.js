// Runs before the page paints, so the saved color theme never flashes the wrong one.
(function () {
  var theme = null;
  try { theme = window.localStorage.getItem('jp-theme'); } catch (e) { /* storage blocked: follow the system */ }
  if (theme !== 'light' && theme !== 'dark') {
    theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  document.documentElement.setAttribute('data-theme', theme);
})();
