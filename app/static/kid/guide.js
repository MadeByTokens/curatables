/* The guide player: one step at a time, the finger gliding to what to
   tap, and the next step after a few seconds (or with the buttons).
   Without this script the steps are simply listed one under another.
   ES3 only: it runs on old tablets. Reduce Motion: no gliding, and the
   steps wait for a tap instead of moving on by themselves. */
(function () {
  var box = document.getElementById('guide');
  if (!box || !box.getElementsByClassName) { return; }
  var steps = box.getElementsByClassName('guide-step');
  var count = document.getElementById('guide-count');
  var playBtn = document.getElementById('guide-play');
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var at = 0, timer = null, playing = !still, started = false;
  var WAIT = 4500;

  function finger(i) {
    var f = steps[i].getElementsByClassName('guide-finger');
    return f.length ? f[0] : null;
  }

  function show(i) {
    var prev = started ? finger(at) : null, next;
    started = true;
    steps[at].className = 'guide-step';
    at = i;
    steps[at].className = 'guide-step is-shown';
    next = finger(at);
    if (next && !still) {
      var left = next.getAttribute('data-left') + '%', top = next.getAttribute('data-top') + '%';
      next.className = 'guide-finger';
      next.style.left = prev ? prev.style.left : '50%';
      next.style.top = prev ? prev.style.top : '90%';
      setTimeout(function () {
        next.style.left = left;
        next.style.top = top;
        setTimeout(function () { next.className = 'guide-finger is-tapping'; }, 900);
      }, 60);
    }
    if (count) { count.innerHTML = (at + 1) + ' / ' + steps.length; }
    schedule();
  }

  function schedule() {
    if (timer) { clearTimeout(timer); timer = null; }
    if (playing && at < steps.length - 1) {
      timer = setTimeout(function () { show(at + 1); }, WAIT);
    }
    if (playBtn) {
      playBtn.innerHTML = playing ? playBtn.getAttribute('data-pause') : playBtn.getAttribute('data-play');
    }
  }

  box.className += ' is-player';
  document.getElementById('guide-prev').onclick = function () { playing = false; show(at > 0 ? at - 1 : 0); };
  document.getElementById('guide-next').onclick = function () {
    playing = false;
    show(at < steps.length - 1 ? at + 1 : 0);
  };
  if (playBtn) {
    playBtn.onclick = function () {
      playing = !playing;
      if (playing && at === steps.length - 1) { show(0); } else { schedule(); }
    };
  }
  for (var i = 0; i < steps.length; i++) {
    var f = finger(i);
    if (f) { f.style.left = f.getAttribute('data-left') + '%'; f.style.top = f.getAttribute('data-top') + '%'; }
  }
  show(0);
})();
