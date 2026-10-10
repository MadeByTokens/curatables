(function () {
  var form = document.getElementById('review-form');
  var addBtn = document.getElementById('add-btn');
  var hint = document.getElementById('add-btn-hint');
  var boxes = form.querySelectorAll('.watched-cb');

  var progress = document.getElementById('review-progress');
  var count = document.getElementById('review-count');
  var nextBtn = document.getElementById('next-unwatched');

  function refreshGate() {
    var watched = 0;
    for (var i = 0; i < boxes.length; i++) {
      var card = document.getElementById('card-' + extractVid(boxes[i]));
      if (boxes[i].checked) {
        watched++;
        if (card) card.classList.add('is-watched');
      } else {
        if (card) card.classList.remove('is-watched');
      }
    }
    var left = boxes.length - watched;
    addBtn.disabled = watched === 0;
    addBtn.textContent = watched === 0 || left === 0
      ? 'Add ' + boxes.length + ' video(s)'
      : 'Add ' + watched + ' watched video(s)';
    if (hint) {
      hint.hidden = left === 0;
      if (watched > 0) {
        hint.textContent = left + ' not watched yet will be left out.';
      }
    }
    if (progress) {
      progress.hidden = false;
      count.textContent = watched + ' of ' + boxes.length + ' watched';
      nextBtn.hidden = left === 0;
    }
  }

  if (nextBtn) {
    nextBtn.addEventListener('click', function () {
      for (var i = 0; i < boxes.length; i++) {
        if (!boxes[i].checked) {
          var card = document.getElementById('card-' + extractVid(boxes[i]));
          var watch = card && card.querySelector('.watch-btn');
          if (watch) { watch.click(); } else if (card) { card.scrollIntoView(); }
          return;
        }
      }
    });
  }

  function extractVid(cb) {
    return cb.name.replace(/^watched_/, '');
  }

  for (var i = 0; i < boxes.length; i++) {
    boxes[i].addEventListener('change', refreshGate);
  }

  // Several videos share one player: each Watch button loads its video
  // there, as an iframe when the site can be embedded (data-embed) and
  // as a link to the original page otherwise.
  var player = document.getElementById('review-player');
  var current = document.getElementById('review-current');
  var watchBtns = form.querySelectorAll('.watch-btn');

  function escapeHtml(s) {
    return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
                     .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
                     .replace(/'/g, '&#39;');
  }

  for (var j = 0; j < watchBtns.length; j++) {
    watchBtns[j].addEventListener('click', function () {
      var title = this.getAttribute('data-title');
      var embed = this.getAttribute('data-embed');
      var orig = this.getAttribute('data-orig');
      var ext = this.getAttribute('data-extractor');
      if (!player) return;

      if (embed) {
        // Some sites (YouTube) ignore autoplay; most honour it.
        var sep = embed.indexOf('?') === -1 ? '?' : '&';
        player.innerHTML =
          '<iframe src="' + escapeHtml(embed) + sep + 'autoplay=1" ' +
          'sandbox="allow-scripts allow-same-origin allow-presentation allow-popups" ' +
          'allow="encrypted-media; fullscreen; picture-in-picture; autoplay" allowfullscreen></iframe>';
      } else {
        var linkHtml = orig
          ? '<a class="btn-linkout" href="' + escapeHtml(orig) +
            '" target="_blank" rel="noopener noreferrer">Watch on source &#8599;</a>'
          : '<em>No original URL recorded.</em>';
        player.innerHTML =
          '<div class="placeholder fd-col p-20">' +
          '  <p class="embed-note-title"><strong>Embed video player not available.</strong></p>' +
          '  <p class="embed-note">Click the button below to watch the original URL (opens in a new tab).</p>' +
          linkHtml +
          (ext ? '<div class="mt-10 fs-12 muted-5">source: ' + escapeHtml(ext) + '</div>' : '') +
          '</div>';
      }

      if (current) current.textContent = 'Now playing: ' + title;
      player.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  refreshGate();
})();
