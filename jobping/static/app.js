// Small helpers. No inline scripts are allowed by the page's security policy.
(function () {
  // Ask before a destructive form is sent: <form data-confirm="Are you sure?">
  document.addEventListener('submit', function (event) {
    var form = event.target;
    var message = form.getAttribute && form.getAttribute('data-confirm');
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });

  // On phones the tab bar is fixed to the bottom of the screen. Scroll a little so a control
  // the keyboard focuses is never hidden behind it.
  var tabBar = document.querySelector('.mainnav');
  if (tabBar) {
    document.addEventListener('focusin', function (event) {
      var target = event.target;
      if (window.getComputedStyle(tabBar).position !== 'fixed' || tabBar.contains(target)) return;
      // Wait a frame: the browser's own scroll-into-view runs after the focus event.
      window.requestAnimationFrame(function () {
        var overlap = target.getBoundingClientRect().bottom - tabBar.getBoundingClientRect().top;
        if (overlap > 0) window.scrollBy(0, overlap + 16);
      });
    });
  }

  // Light/dark switch. The button stays hidden unless this script runs, because it needs it.
  var toggle = document.querySelector('[data-theme-toggle]');
  if (toggle) {
    var root = document.documentElement;
    var label = function () {
      toggle.setAttribute('aria-label', root.getAttribute('data-theme') === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
    };
    toggle.hidden = false;
    label();
    toggle.addEventListener('click', function () {
      var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { window.localStorage.setItem('jp-theme', next); } catch (e) { /* not saved, still switched for this visit */ }
      label();
    });
  }
})();
