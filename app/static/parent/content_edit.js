(function() {
  var input = document.getElementById('thumb-input');
  var preview = document.getElementById('thumb-preview');
  if (!input || !preview || typeof FileReader === 'undefined') return;
  input.onchange = function(e) {
    var files = e.target && e.target.files;
    if (!files || !files[0]) return;
    var reader = new FileReader();
    reader.onload = function(ev) { preview.src = ev.target.result; };
    reader.readAsDataURL(files[0]);
  };
})();
