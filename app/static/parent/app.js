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
