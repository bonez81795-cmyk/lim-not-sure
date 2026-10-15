// Simulator: shows both phones and refreshes them. Message text is added with
// textContent, never as HTML, so nothing a "customer" types can run as code.
(function () {
  var root = document.getElementById('sim');
  if (!root) return;
  var feedUrl = root.getAttribute('data-feed');
  var phone = root.getAttribute('data-phone');
  var custFeed = document.getElementById('cust-feed');
  var ownFeed = document.getElementById('own-feed');
  var nowOut = document.getElementById('sim-now');
  var bookLink = document.getElementById('book-link');
  var errorBox = document.getElementById('sim-error');
  var lastKey = '';

  function bubbles(container, list, emptyText, mineClass) {
    container.textContent = '';
    if (!list.length) {
      var p = document.createElement('p');
      p.className = 'empty';
      p.textContent = emptyText;
      container.appendChild(p);
      return;
    }
    list.forEach(function (m) {
      var div = document.createElement('div');
      div.className = 'bubble ' + (m.mine && mineClass ? 'mine' : 'them');
      div.textContent = m.body;
      if (m.note) {
        var note = document.createElement('span');
        note.className = 'bubble-note';
        note.textContent = m.note;
        div.appendChild(note);
      }
      container.appendChild(div);
    });
    container.scrollTop = container.scrollHeight;
  }

  function refresh() {
    return fetch(feedUrl + '?phone=' + encodeURIComponent(phone), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var key = JSON.stringify([data.customer, data.owner, data.book_path]);
        nowOut.textContent = data.now;
        if (key !== lastKey) {
          lastKey = key;
          bubbles(custFeed, data.customer, 'No texts yet. Start with a missed call.', true);
          bubbles(ownFeed, data.owner, 'Alerts to you show up here.', false);
          if (data.book_path) {
            bookLink.href = data.book_path;
            bookLink.hidden = false;
          } else {
            bookLink.hidden = true;
          }
        }
      })
      .catch(function () { /* try again on the next tick */ });
  }

  document.querySelectorAll('form[data-sim]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      errorBox.hidden = true;
      fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'fetch' }
      })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          if (!res.ok) {
            errorBox.textContent = res.error || 'That did not work.';
            errorBox.hidden = false;
          } else {
            var input = form.querySelector('input[name=body]');
            if (input) input.value = '';
          }
          return refresh();
        })
        .catch(function () {
          errorBox.textContent = 'Could not reach the app. Reload the page.';
          errorBox.hidden = false;
        });
    });
  });

  refresh();
  window.setInterval(refresh, 2500);
})();
