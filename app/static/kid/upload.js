/* Kid upload: plain XHR multipart with progress. ES3 only — no fetch,
   no const/let, no arrow functions. Runs on iOS 9 Safari. */
(function () {
  var form = document.getElementById('upload-form');
  var MAX_BYTES = parseInt(form.getAttribute('data-max-bytes'), 10);
  var fileInput = document.getElementById('file');
  var progressWrap = document.getElementById('progress-wrap');
  var bar = document.getElementById('bar');
  var status = document.getElementById('status');
  var startBtn = document.getElementById('start-btn');
  var stopBtn = document.getElementById('stop-btn');
  var xhr = null;

  function humanMB(n) {
    return Math.round(n / 1048576) + ' MB';
  }

  function show(el) {
    el.className = el.className.replace(/(^|\s)d-none(\s|$)/, ' ');
  }

  function hide(el) {
    if (!/(^|\s)d-none(\s|$)/.test(el.className)) { el.className += ' d-none'; }
  }

  // Stop while the file is still on its way. The form keeps the title
  // and channel, so the kid can pick another video or send it again.
  stopBtn.onclick = function () {
    if (!xhr) { return; }
    var stopping = xhr;
    xhr = null;
    stopping.abort();
    hide(stopBtn);
    startBtn.disabled = false;
    bar.value = 0;
    setStatus(kidT('Stopped. Pick another video, or tap Send it again.'));
  };

  function setStatus(msg) {
    show(progressWrap);
    status.innerHTML = '';
    status.appendChild(document.createTextNode(msg));
  }

  form.onsubmit = function (e) {
    if (e && e.preventDefault) { e.preventDefault(); }
    var file = fileInput.files && fileInput.files[0];
    if (!file) {
      setStatus(kidT('Pick a video first.'));
      return false;
    }
    if (file.size > MAX_BYTES) {
      setStatus(kidT('That video is too big ({size}). Ask a grown-up.', {size: humanMB(file.size)}));
      return false;
    }

    startBtn.disabled = true;
    bar.value = 0;
    setStatus(kidT('Sending...'));

    var fd = new FormData(form);
    var mine = new XMLHttpRequest();
    xhr = mine;
    show(stopBtn);
    mine.open('POST', '/upload', true);
    mine.setRequestHeader('X-Requested-With', 'XMLHttpRequest');

    if (mine.upload) {
      mine.upload.onprogress = function (evt) {
        if (xhr !== mine) { return; }
        if (evt.lengthComputable) {
          var pct = Math.floor((evt.loaded / evt.total) * 100);
          bar.value = pct;
          // All sent: the box is getting it ready, too late to stop.
          if (pct >= 100) { hide(stopBtn); }
          setStatus(pct < 100 ? kidT('Sending: {pct}%', {pct: pct}) : kidT('Getting it ready...'));
        }
      };
    }

    mine.onreadystatechange = function () {
      if (mine.readyState !== 4 || xhr !== mine) { return; }
      xhr = null;
      hide(stopBtn);
      startBtn.disabled = false;
      var data = null;
      try { data = JSON.parse(mine.responseText); } catch (err) {}
      if (data && data.redirect) {
        window.location = data.redirect;
      } else if (data && data.error) {
        setStatus(data.error);
      } else if (mine.status === 413) {
        setStatus(kidT('This video is too big. Ask a grown-up to help.'));
      } else {
        setStatus(kidT('That didn\u2019t work. Try again.'));
      }
    };

    mine.send(fd);
    return false;
  };
})();
