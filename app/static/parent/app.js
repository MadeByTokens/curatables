(function () {
  document.addEventListener('submit', function (e) {
    var form = e.target;
    var message = form.getAttribute && form.getAttribute('data-confirm');
    if (message && !window.confirm(message)) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);

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
    var tick = function () {
      get(url, function (status, body) {
        if (status === 200) {
          try {
            var job = JSON.parse(body);
            if (job.state !== state || (job.messages || []).length !== count) {
              location.reload();
              return;
            }
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
    var check = function () {
      get(health, function (status) {
        if (status === 200) { location.href = back; } else { setTimeout(check, 2000); }
      });
    };
    setTimeout(check, parseInt(wait.getAttribute('data-wait-delay') || '6000', 10));
  }
})();
