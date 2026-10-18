(function () {
  document.addEventListener('submit', function (e) {
    var form = e.target;
    var message = form.getAttribute && form.getAttribute('data-confirm');
    if (message && !window.confirm(message)) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);

  document.addEventListener('submit', function (e) {
    var form = e.target;
    var message = form.getAttribute && form.getAttribute('data-busy');
    if (!message || e.defaultPrevented) { return; }
    var button = form.querySelector('button[type="submit"], button:not([type])');
    if (button) {
      button.setAttribute('data-idle-text', button.textContent);
      // Disabled after this event, so the button's own name/value still goes with the form.
      setTimeout(function () { button.disabled = true; }, 0);
      button.textContent = message;
    }
    form.setAttribute('aria-busy', 'true');
  });

  // Back from the next page can show this one as it was left: busy.
  window.addEventListener('pageshow', function () {
    var buttons = document.querySelectorAll('[data-idle-text]');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].textContent = buttons[i].getAttribute('data-idle-text');
      buttons[i].removeAttribute('data-idle-text');
      buttons[i].disabled = false;
      buttons[i].form.removeAttribute('aria-busy');
    }
  });

  document.addEventListener('change', function (e) {
    var ids = e.target.getAttribute && e.target.getAttribute('data-show-password');
    if (!ids) { return; }
    ids.split(' ').forEach(function (id) {
      var field = document.getElementById(id);
      if (field) { field.type = e.target.checked ? 'text' : 'password'; }
    });
  });

  function showPinPanel() {
    var picked = document.querySelector('input[name="pin_style"]:checked');
    if (!picked) { return; }
    var panels = document.querySelectorAll('[data-pin-panel]');
    for (var i = 0; i < panels.length; i++) {
      panels[i].hidden = panels[i].getAttribute('data-pin-panel') !== picked.value;
    }
  }
  document.addEventListener('change', function (e) {
    if (e.target.name === 'pin_style') { showPinPanel(); }
  });

  // Ticking a channel means "only the ones ticked here".
  document.addEventListener('change', function (e) {
    if (e.target.name !== 'channel_ids' || !e.target.checked) { return; }
    var some = e.target.form.querySelector('input[name="channel_access"][value="some"]');
    if (some) { some.checked = true; }
  });
  showPinPanel();

  function openTargetDetails() {
    var target = location.hash && document.getElementById(location.hash.slice(1));
    if (target && target.tagName === 'DETAILS') { target.open = true; }
  }
  window.addEventListener('hashchange', openTargetDetails);
  openTargetDetails();
})();

(function () {
  function get(url, done) {
    var xhr = new XMLHttpRequest();
    xhr.open('GET', url + (url.indexOf('?') < 0 ? '?' : '&') + 't=' + Date.now(), true);
    xhr.timeout = 8000;
    xhr.onload = function () { done(xhr.status, xhr.responseText); };
    xhr.onerror = xhr.ontimeout = function () { done(0, ''); };
    xhr.send();
  }

  var poll = document.querySelector('[data-job-poll]');
  if (poll) {
    var url = poll.getAttribute('data-job-poll');
    var state = poll.getAttribute('data-job-state');
    var count = parseInt(poll.getAttribute('data-job-count') || '0', 10);
    var cancellable = poll.getAttribute('data-job-cancellable') === '1';
    var progress = document.querySelector('[data-job-progress]');
    var tick = function () {
      get(url, function (status, body) {
        if (status === 200) {
          try {
            var job = JSON.parse(body);
            if (job.state !== state || (job.messages || []).length !== count ||
                (state === 'running' && !!job.cancellable !== cancellable)) {
              location.reload();
              return;
            }
            if (progress) { progress.textContent = job.progress || ''; }
          } catch (e) { /* keep polling */ }
        }
        setTimeout(tick, 2000);
      });
    };
    setTimeout(tick, 2000);
  }

  var wait = document.querySelector('[data-wait-healthy]');
  if (wait) {
    var health = wait.getAttribute('data-wait-healthy');
    var back = wait.getAttribute('data-wait-back');
    var mustGoDown = wait.getAttribute('data-wait-down') === '1';
    var slow = document.querySelector('[data-wait-slow]');
    var started = Date.now();
    var wentDown = false;
    var check = function () {
      get(health, function (status) {
        var elapsed = Date.now() - started;
        if (status !== 200) { wentDown = true; }
        if (status === 200 && (wentDown || !mustGoDown || elapsed > 90000)) {
          location.href = back;
          return;
        }
        if (slow && elapsed > 240000) { slow.hidden = false; }
        setTimeout(check, 2000);
      });
    };
    setTimeout(check, parseInt(wait.getAttribute('data-wait-delay') || '4000', 10));
  }
})();
