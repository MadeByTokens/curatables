/* On-screen PIN pad (ES3; works on iOS 9). The real <input> stays in the
   form for no-JS browsers; with JS it is hidden and filled by the pad so
   the tablet keyboard never pops up. */
(function () {
  var input = document.getElementById('pin');
  var pad = document.getElementById('pin-pad');
  var dots = document.getElementById('pin-dots');
  if (!input || !pad || !dots) { return; }
  var max = parseInt(pad.getAttribute('data-max'), 10) || 6;
  var autoSubmit = pad.getAttribute('data-auto-submit') === '1';
  var shown = [];
  var SHOW_MS = 900;
  var hideTimer = null;

  document.documentElement.className += ' js';
  input.setAttribute('readonly', 'readonly');

  function render() {
    var out = '';
    for (var i = 0; i < input.value.length; i++) { out += (shown[i] || '●') + ' '; }
    dots.innerHTML = out || '&nbsp;';
  }

  pad.onclick = function (e) {
    var el = (e || window.event).target || (e || window.event).srcElement;
    while (el && el !== pad && !el.getAttribute('data-digit')) { el = el.parentNode; }
    if (!el || el === pad) { return; }
    var d = el.getAttribute('data-digit');
    if (d === 'back') {
      input.value = input.value.substring(0, input.value.length - 1);
      shown = [];
    } else if (input.value.length < max) {
      input.value += d;
      shown = [];
      shown[input.value.length - 1] = el.getAttribute('data-show');
      if (hideTimer) { clearTimeout(hideTimer); }
      hideTimer = setTimeout(function () { shown = []; render(); }, SHOW_MS);
    }
    render();
    if (autoSubmit && d !== 'back' && input.value.length === max) {
      input.form.submit();
    }
  };
  render();
})();
