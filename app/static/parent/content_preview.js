(function () {
  var form = document.getElementById('review-form');
  var addBtn = document.getElementById('add-btn');
  var hint = document.getElementById('add-btn-hint');
  var boxes = form.querySelectorAll('.watched-cb');

  function refreshGate() {
    var allChecked = true;
    for (var i = 0; i < boxes.length; i++) {
      var card = document.getElementById('card-' + extractVid(boxes[i]));
      if (boxes[i].checked) {
        if (card) card.classList.add('is-watched');
      } else {
        if (card) card.classList.remove('is-watched');
        allChecked = false;
      }
    }
    addBtn.disabled = !allChecked;
    if (hint) hint.style.display = allChecked ? 'none' : 'inline-block';
  }

  function extractVid(cb) {
    // name is "watched_<videoid>"
    return cb.name.replace(/^watched_/, '');
  }

  for (var i = 0; i < boxes.length; i++) {
    boxes[i].addEventListener('change', refreshGate);
  }

  // Multi-video: wire the "Watch" buttons to swap the shared player
  // pane. For platforms in the Tier 1 iframe allow-list the button
  // carries a data-embed URL and we render an iframe; for everything
  // else we render a link-out card pointing at data-orig, opened in
  // a new tab.
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
        // Iframe path — append autoplay so clicking Watch feels
        // responsive. Some sites (YouTube) ignore the param but
        // most honour it.
        var sep = embed.indexOf('?') === -1 ? '?' : '&';
        player.innerHTML =
          '<iframe src="' + escapeHtml(embed) + sep + 'autoplay=1" ' +
          'allow="encrypted-media; fullscreen; picture-in-picture; autoplay" allowfullscreen></iframe>';
      } else {
        // Link-out fallback — no clean iframe for this platform.
        var linkHtml = orig
          ? '<a class="btn-linkout" href="' + escapeHtml(orig) +
            '" target="_blank" rel="noopener noreferrer">Watch on source &#8599;</a>'
          : '<em>No original URL recorded.</em>';
        player.innerHTML =
          '<div class="placeholder fd-col p-20">' +
          '  <p class="embed-note-title"><strong>Embed video player not available.</strong></p>' +
          '  <p class="embed-note">Click below to watch the original URL (opens in a new tab).</p>' +
          linkHtml +
          (ext ? '<div class="mt-10 fs-12 muted-5">source: ' + escapeHtml(ext) + '</div>' : '') +
          '</div>';
      }

      if (current) current.textContent = 'Now playing: ' + title;
      // Scroll the player into view so the parent doesn't have to hunt for it
      player.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  refreshGate();
})();
