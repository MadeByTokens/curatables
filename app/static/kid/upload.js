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

  function humanMB(n) {
    return Math.round(n / 1048576) + ' MB';
  }

  function show(el) {
    el.className = el.className.replace(/(^|\s)d-none(\s|$)/, ' ');
  }

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
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/upload', true);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');

    if (xhr.upload) {
      xhr.upload.onprogress = function (evt) {
        if (evt.lengthComputable) {
          var pct = Math.floor((evt.loaded / evt.total) * 100);
          bar.value = pct;
          setStatus(pct < 100 ? kidT('Sending: {pct}%', {pct: pct}) : kidT('Getting it ready...'));
        }
      };
    }

    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) { return; }
      startBtn.disabled = false;
      var data = null;
      try { data = JSON.parse(xhr.responseText); } catch (err) {}
      if (data && data.redirect) {
        window.location = data.redirect;
      } else if (data && data.error) {
        setStatus(data.error);
      } else if (xhr.status === 413) {
        setStatus(kidT('This video is too big. Ask a grown-up to help.'));
      } else {
        setStatus(kidT('That didn\u2019t work. Try again.'));
      }
    };

    xhr.send(fd);
    return false;
  };
})();
