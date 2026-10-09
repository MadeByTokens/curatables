function toggleAll(source) {
  var boxes = document.querySelectorAll('input[form="bulk-form"][name="video_ids"]');
  for (var i = 0; i < boxes.length; i++) { boxes[i].checked = source.checked; }
}
function confirmBulk() {
  var btn = document.activeElement;
  var action = btn && btn.name === 'action' ? btn.value : '';
  var boxes = document.querySelectorAll('input[form="bulk-form"][name="video_ids"]:checked');
  if (boxes.length === 0) {
    alert('Pick at least one video first.');
    return false;
  }
  if (action === 'delete') {
    return confirm('Delete ' + boxes.length + ' video(s)? This removes the files on disk.');
  }
  return true;
}
(function () {
  var form = document.getElementById('bulk-form');
  var all = document.getElementById('select-all');
  if (form) {
    form.addEventListener('submit', function (e) {
      if (!confirmBulk()) { e.preventDefault(); }
    });
  }
  if (all) {
    all.addEventListener('change', function () { toggleAll(all); });
  }
})();

/* While downloads run, poll their status instead of reloading the page
   (a reload would wipe ticked boxes and scroll position). */
(function () {
  var cards = document.querySelectorAll('.card[data-download-status="pending"], .card[data-download-status="downloading"]');
  if (!cards.length) { return; }
  var ids = [];
  for (var i = 0; i < cards.length; i++) { ids.push(cards[i].getAttribute('data-video-id')); }
  function busy() {
    return document.querySelector('input[name="video_ids"]:checked') ||
           (document.activeElement && /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName));
  }
  function poll() {
    fetch('/parent/content/status?ids=' + encodeURIComponent(ids.join(',')), {credentials: 'same-origin'})
      .then(function (r) { return r.json(); })
      .then(function (statuses) {
        var changed = false;
        for (var i = 0; i < cards.length; i++) {
          var id = cards[i].getAttribute('data-video-id');
          if (statuses[id] && statuses[id] !== cards[i].getAttribute('data-download-status')) {
            changed = true;
          }
        }
        if (!changed) { setTimeout(poll, 5000); return; }
        if (busy()) {
          document.getElementById('refresh-notice').hidden = false;
        } else {
          window.location.reload();
        }
      })
      .catch(function () { setTimeout(poll, 15000); });
  }
  setTimeout(poll, 5000);
})();
