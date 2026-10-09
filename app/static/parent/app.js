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
})();
