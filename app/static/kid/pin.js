/* On-screen PIN pad (ES3; works on iOS 9). The real <input> stays in the
   form for no-JS browsers; with JS it is hidden and filled by the pad so
   the tablet keyboard never pops up. */
(function () {
  var input = document.getElementById('pin');
  var pad = document.getElementById('pin-pad');
  var dots = document.getElementById('pin-dots');
  if (!input || !pad || !dots) { return; }

  document.documentElement.className += ' js';
  input.setAttribute('readonly', 'readonly');

  function render() {
    var out = '';
    for (var i = 0; i < input.value.length; i++) { out += '● '; }
    dots.innerHTML = out || '&nbsp;';
  }

  pad.onclick = function (e) {
    var el = (e || window.event).target || (e || window.event).srcElement;
    while (el && el !== pad && !el.getAttribute('data-digit')) { el = el.parentNode; }
    if (!el || el === pad) { return; }
    var d = el.getAttribute('data-digit');
    if (d === 'back') {
      input.value = input.value.substring(0, input.value.length - 1);
    } else if (input.value.length < 6) {
      input.value += d;
    }
    render();
  };
  render();
})();
