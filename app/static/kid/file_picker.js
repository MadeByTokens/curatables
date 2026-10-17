/* Shows the chosen file's name beside a kid file picker (ES3). */
(function () {
  var inputs = document.getElementsByTagName('input');
  function show() {
    var target = document.getElementById(this.getAttribute('data-picker-name'));
    var name = (this.value || '').split(/[\\\/]/).pop();
    if (!target || !name) { return; }
    target.innerHTML = '';
    target.appendChild(document.createTextNode(name));
  }
  for (var i = 0; i < inputs.length; i++) {
    if (inputs[i].getAttribute('data-picker-name')) {
      if (inputs[i].addEventListener) {
        inputs[i].addEventListener('change', show, false);
      } else {
        inputs[i].onchange = show;
      }
    }
  }
})();
