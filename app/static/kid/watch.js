/* Watch page behaviour. ES3 only: this must run on iOS 9 Safari.
   Everything that talks to the server uses XHR so the <video> element is
   never re-created (a reload would restart playback). Forms still work
   without JS through their normal POST + redirect. */
(function () {
  var root = document.getElementById('watch');
  if (!root) { return; }
  var videoId = root.getAttribute('data-video-id');
  var csrfToken = root.getAttribute('data-csrf');
  var autoSubs = root.getAttribute('data-auto-subs') === '1';
  var video = document.getElementById('player');
  var statusEl = document.getElementById('action-status');

  function trim(s) { return s.replace(/^\s+|\s+$/g, ''); }

  function hasClass(el, name) {
    return (' ' + el.className + ' ').indexOf(' ' + name + ' ') !== -1;
  }
  function addClass(el, name) { if (!hasClass(el, name)) { el.className += ' ' + name; } }
  function removeClass(el, name) {
    el.className = trim((' ' + el.className + ' ').replace(' ' + name + ' ', ' '));
  }

  function showStatus(text) {
    if (!statusEl) { return; }
    statusEl.innerHTML = '';
    if (text) {
      statusEl.appendChild(document.createTextNode('⚠️ ' + text));
    }
  }

  /* POST form-encoded `body`; calls done(xhr) on 2xx, else shows a
     friendly message (429 = slow down, anything else = try again). */
  function post(url, body, done) {
    var xhr = new XMLHttpRequest();
    xhr.open('POST', url, true);
    xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
    xhr.setRequestHeader('X-CSRF-Token', csrfToken);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) { return; }
      if (xhr.status >= 200 && xhr.status < 300) {
        showStatus('');
        if (done) { done(xhr); }
      } else if (xhr.status === 429) {
        showStatus(kidT('Too many in a row. Wait a minute, then try again.'));
      } else if (xhr.status === 403) {
        showStatus(kidT('That didn’t work. Ask a grown-up to reload the page.'));
      } else {
        showStatus(kidT('That didn’t work. Try again.'));
      }
    };
    xhr.send(body);
  }

  function nodeFromHtml(html) {
    var tmp = document.createElement('div');
    tmp.innerHTML = html;
    var node = tmp.firstChild;
    while (node && node.nodeType !== 1) { node = node.nextSibling; }
    return node;
  }

  function closest(el, attr) {
    while (el && el.nodeType === 1) {
      if (el.getAttribute(attr) !== null) { return el; }
      el = el.parentNode;
    }
    return null;
  }

  /* --- Event log ------------------------------------------------- */
  function sendLog(event, extra) {
    var body = 'event=' + encodeURIComponent(event) + '&video_id=' + encodeURIComponent(videoId);
    if (extra) {
      for (var key in extra) {
        if (extra.hasOwnProperty(key)) {
          body += '&' + encodeURIComponent(key) + '=' + encodeURIComponent(extra[key]);
        }
      }
    }
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/log', true);
    xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
    xhr.send(body);
  }

  /* --- Reactions ------------------------------------------------- */
  function applyReactions(data) {
    var bar = document.getElementById('reaction-bar');
    if (!bar || !data) { return; }
    var btns = bar.getElementsByTagName('button');
    for (var i = 0; i < btns.length; i++) {
      var name = btns[i].getAttribute('data-reaction');
      var on = data.mine === name;
      if (on) { addClass(btns[i], 'active'); } else { removeClass(btns[i], 'active'); }
      btns[i].setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    var counts = bar.getElementsByTagName('span');
    for (var j = 0; j < counts.length; j++) {
      var key = counts[j].getAttribute('data-count-for');
      if (key) {
        var n = data.counts && data.counts[key];
        counts[j].innerHTML = n ? String(n) : '';
      }
    }
  }

  var reactionBar = document.getElementById('reaction-bar');
  if (reactionBar) {
    reactionBar.onclick = function (e) {
      var target = (e || window.event).target || (e || window.event).srcElement;
      var btn = closest(target, 'data-reaction');
      if (!btn) { return; }
      var name = btn.getAttribute('data-reaction');
      post('/api/react',
           'video_id=' + encodeURIComponent(videoId) + '&emoji=' + encodeURIComponent(name),
           function (xhr) {
             var data = null;
             try { data = JSON.parse(xhr.responseText); } catch (err) {}
             applyReactions(data);
           });
    };
  }

  /* --- Forms: comments and tags --------------------------------- */
  /* Each thread's reply box stays folded behind its Reply button
     until tapped, so a thread reads as a thread on a phone. */
  function foldReplyForms(scope) {
    var buttons = scope.getElementsByTagName('button');
    for (var i = 0; i < buttons.length; i++) {
      var id = buttons[i].getAttribute('data-reply-for');
      if (!id) { continue; }
      var form = document.querySelector('form[data-reply-form="' + id + '"]');
      if (!form) { continue; }
      addClass(form, 'd-none');
      removeClass(buttons[i], 'd-none');
      buttons[i].onclick = openReply;
    }
  }
  function openReply() {
    var btn = this;
    var form = document.querySelector('form[data-reply-form="' +
                                      btn.getAttribute('data-reply-for') + '"]');
    if (!form) { return; }
    removeClass(form, 'd-none');
    addClass(btn, 'd-none');
    if (form.elements['body'].focus) { form.elements['body'].focus(); }
  }
  foldReplyForms(document);

  function insertComment(html) {
    var node = nodeFromHtml(html);
    if (!node) { return; }
    var parentId = node.getAttribute('data-parent-id');
    var target = null;
    if (parentId) {
      target = document.querySelector('.replies[data-replies-for="' + parentId + '"]');
    }
    if (!target) { target = document.getElementById('comment-list'); }
    if (target) { target.appendChild(node); }
    foldReplyForms(node);
    var empty = document.getElementById('no-comments');
    if (empty) { addClass(empty, 'd-none'); }
    var count = document.getElementById('comment-count');
    if (count) {
      var total = parseInt(count.getAttribute('data-total'), 10) || 0;
      total += 1;
      count.setAttribute('data-total', String(total));
      count.innerHTML = '(' + total + ')';
    }
  }

  var handlers = {
    'comment': function (form) {
      var field = form.elements['body'];
      var text = trim(field.value);
      if (!text) { return; }
      var body = 'body=' + encodeURIComponent(text);
      var parent = form.elements['parent_comment_id'];
      if (parent && parent.value) {
        body += '&parent_comment_id=' + encodeURIComponent(parent.value);
      }
      post(form.action, body, function (xhr) {
        if (xhr.status === 200) { insertComment(xhr.responseText); }
        field.value = '';
      });
    },
    'tag-add': function (form) {
      var input = form.elements['tag'];
      var val = trim(input.value);
      input.value = '';
      if (!val) { return; }
      post(form.action, 'tag=' + encodeURIComponent(val), function (xhr) {
        if (xhr.status !== 200) { return; }
        var node = nodeFromHtml(xhr.responseText);
        var list = document.getElementById('tag-list');
        if (node && list) { list.appendChild(node); }
      });
    },
    'tag-remove': function (form) {
      var chip = form.parentNode;
      post(form.action, 'tag_id=' + encodeURIComponent(form.elements['tag_id'].value),
           function () {
             if (chip && chip.parentNode) { chip.parentNode.removeChild(chip); }
           });
    }
  };

  function onSubmit(e) {
    var form = e.target || e.srcElement;
    var kind = form && form.getAttribute && form.getAttribute('data-xhr');
    if (!kind || !handlers[kind]) { return; }
    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
    handlers[kind](form);
  }
  if (document.addEventListener) {
    document.addEventListener('submit', onSubmit, false);
  }

  /* --- Carry on watching ------------------------------------------
     Where each kid stopped is kept on this device only, newest first,
     for the last RESUME_KEEP videos. */
  var RESUME_KEEP = 50;
  var RESUME_MIN_SECONDS = 10;
  var RESUME_END_MARGIN = 15;
  var RESUME_SAVE_EVERY_MS = 5000;

  function resumeSpot(profileId) {
    var key = 'curatables.resume.' + profileId;
    var card = document.getElementById('resume-card');
    var lastSave = 0;

    function load() {
      try {
        var list = JSON.parse(window.localStorage.getItem(key) || '[]');
        return list instanceof Array ? list : [];
      } catch (err) { return []; }
    }
    function save(list) {
      try { window.localStorage.setItem(key, JSON.stringify(list.slice(0, RESUME_KEEP))); } catch (err) {}
    }
    function without(list) {
      var out = [];
      for (var i = 0; i < list.length; i++) {
        if (list[i] && list[i].v !== videoId) { out.push(list[i]); }
      }
      return out;
    }
    function saved() {
      var list = load();
      for (var i = 0; i < list.length; i++) {
        if (list[i] && list[i].v === videoId && list[i].t >= RESUME_MIN_SECONDS) { return list[i].t; }
      }
      return 0;
    }
    function worthKeeping(t) {
      var d = video.duration;
      return t >= RESUME_MIN_SECONDS && !(d > 0 && t > d - RESUME_END_MARGIN);
    }
    function seek(t) {
      if (video.readyState >= 1) {
        try { video.currentTime = t; } catch (err) {}
        return;
      }
      var once = function () {
        video.removeEventListener('loadedmetadata', once, false);
        try { video.currentTime = t; } catch (err) {}
      };
      video.addEventListener('loadedmetadata', once, false);
    }

    if (!profileId || !card || !window.localStorage) {
      return { offer: function () {}, hideCard: function () {},
               remember: function () {}, forget: function () {} };
    }
    return {
      offer: function () {
        var t = saved();
        if (!t) { return; }
        removeClass(card, 'd-none');
        document.getElementById('resume-carry-on').onclick = function () {
          addClass(card, 'd-none');
          seek(t);
          video.play();
        };
        document.getElementById('resume-from-start').onclick = function () {
          addClass(card, 'd-none');
          video.play();
        };
      },
      hideCard: function () { addClass(card, 'd-none'); },
      remember: function (now) {
        var stamp = new Date().getTime();
        if (!now && stamp - lastSave < RESUME_SAVE_EVERY_MS) { return; }
        lastSave = stamp;
        var t = Math.floor(video.currentTime || 0);
        var list = without(load());
        if (worthKeeping(t)) { list.unshift({ v: videoId, t: t }); }
        save(list);
      },
      forget: function () { save(without(load())); }
    };
  }

  /* --- Player ---------------------------------------------------- */
  sendLog('video_view');
  if (!video) { return; }

  var loggedPlay = false;
  var endCard = document.getElementById('end-card');
  var resume = resumeSpot(root.getAttribute('data-profile-id'));

  video.onplay = function () {
    if (endCard) { addClass(endCard, 'd-none'); }
    resume.hideCard();
    if (!loggedPlay) {
      sendLog('video_play');
      loggedPlay = true;
    }
  };

  video.ontimeupdate = function () { resume.remember(false); };
  video.onpause = function () { resume.remember(true); };

  video.onended = function () {
    var watchSeconds = 0;
    try { watchSeconds = Math.floor(video.duration); } catch (err) {}
    sendLog('video_complete', { watch_seconds: watchSeconds });
    resume.forget();
    if (endCard) { removeClass(endCard, 'd-none'); }
  };

  resume.offer();

  var again = document.getElementById('play-again');
  if (again) {
    again.onclick = function () {
      try { video.currentTime = 0; } catch (err) {}
      video.play();
    };
  }

  var subXhr = new XMLHttpRequest();
  subXhr.open('GET', '/media/subs/' + encodeURIComponent(videoId), true);
  subXhr.onreadystatechange = function () {
    if (subXhr.readyState !== 4 || subXhr.status !== 200) { return; }
    var subs = [];
    try { subs = JSON.parse(subXhr.responseText); } catch (err) {}
    for (var i = 0; i < subs.length; i++) {
      var track = document.createElement('track');
      track.kind = 'subtitles';
      track.label = subs[i].label || subs[i].lang;
      if (subs[i].lang) { track.srclang = subs[i].lang; }
      track.src = subs[i].url;
      if (i === 0 && autoSubs) { track['default'] = true; }
      video.appendChild(track);
    }
  };
  subXhr.send();
})();
