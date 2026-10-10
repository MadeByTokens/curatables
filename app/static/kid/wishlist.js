/* Find new videos: show "Looking…" as soon as a search starts and ignore
   more taps until the next page arrives. ES3 for old tablets. */
(function () {
  var busy = false;
  var looking = document.getElementById('wishlist-looking');

  function start() {
    if (busy) { return false; }
    busy = true;
    document.body.className += ' wishlist-busy';
    if (looking) { looking.className += ' is-on'; }
    return true;
  }

  function stop() {
    busy = false;
    document.body.className = document.body.className.replace(/\s*wishlist-busy/g, '');
    if (looking) { looking.className = looking.className.replace(/\s*is-on/g, ''); }
  }

  var els = document.querySelectorAll('[data-looking]');
  for (var i = 0; i < els.length; i++) {
    if (els[i].tagName === 'FORM') {
      els[i].onsubmit = start;
    } else {
      els[i].onclick = start;
    }
  }

  // Tapping Videos or Channels searches again right away.
  var form = document.querySelector('form.wishlist-form');
  var radios = form ? form.querySelectorAll('input[name="mode"]') : [];
  for (var j = 0; j < radios.length; j++) {
    radios[j].onclick = function () {
      if (form.q.value.replace(/\s+/g, '') && start()) { form.submit(); }
    };
  }

  // Coming back with the browser's Back button shows the old page from
  // memory; it must not stay stuck on "Looking…".
  window.onpageshow = function (e) { if (e && e.persisted) { stop(); } };
})();
