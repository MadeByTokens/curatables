(function () {
  var form = document.getElementById('bulk-form');
  var all = document.getElementById('select-all');
  var boxSelector = 'input[form="bulk-form"][name="video_ids"]';

  function confirmBulk(button) {
    var action = button && button.name === 'action' ? button.value : '';
    var picked = document.querySelectorAll(boxSelector + ':checked');
    if (picked.length === 0) {
      alert('Pick at least one video first.');
      return false;
    }
    if (action === 'delete') {
      return confirm('Delete ' + picked.length + ' video(s)? This removes the files on disk.');
    }
    return true;
  }

  if (form) {
    form.addEventListener('submit', function (e) {
      if (!confirmBulk(e.submitter || document.activeElement)) { e.preventDefault(); }
    });
  }
  if (all) {
    all.addEventListener('change', function () {
      var boxes = document.querySelectorAll(boxSelector);
      for (var i = 0; i < boxes.length; i++) { boxes[i].checked = all.checked; }
    });
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
