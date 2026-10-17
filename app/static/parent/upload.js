(function () {
  var startBtn = document.getElementById("start-btn");
  var cancelBtn = document.getElementById("cancel-btn");
  var fileInput = document.getElementById("file");
  var channelSelect = document.getElementById("channel_id");
  var titleInput = document.getElementById("title");
  var progressWrap = document.getElementById("progress-wrap");
  var bar = document.getElementById("bar");
  var status = document.getElementById("status");
  var resumeBanner = document.getElementById("resume-banner");
  var resumeNameEl = document.getElementById("resume-name");
  var resumePctEl = document.getElementById("resume-pct");
  var resumeDismissBtn = document.getElementById("resume-dismiss");
  var currentUpload = null;
  var pendingResume = null;
  var retryCount = 0;

  var form = document.getElementById("upload-form");
  var MAX_BYTES = parseInt(form.getAttribute("data-max-bytes"), 10);
  form.addEventListener("submit", function (e) { e.preventDefault(); });

  function setStatus(msg) {
    status.textContent = msg;
  }

  function reset() {
    currentUpload = null;
    retryCount = 0;
    startBtn.hidden = false;
    cancelBtn.hidden = true;
  }

  function humanSize(n) {
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB";
    return (n / 1073741824).toFixed(2) + " GB";
  }

  // Leave empty values out: Safari rejects an Upload-Metadata header
  // that contains one (tus-js-client issue #41).
  function buildMetadata(file, channelId, title) {
    var m = {
      filename: file.name,
      channel_id: String(channelId),
    };
    if (file.type) m.filetype = file.type;
    if (title) m.title = title;
    return m;
  }

  // Don't retry errors that retrying cannot fix, such as 507 from the
  // free-disk-space check; the default retries would take 39 s.
  function shouldRetry(err, retryAttempt, options) {
    var resp = err && err.originalResponse;
    var code = (resp && resp.getStatus) ? resp.getStatus() : 0;
    if (code === 507 || code === 413 || code === 401 || code === 403 || code === 410) {
      return false;
    }
    retryCount = retryAttempt + 1;
    setStatus("Network blip — retrying (attempt " + retryCount +
              " of " + options.retryDelays.length + ")…");
    return true;
  }

  function buildUpload(file, resumeFrom) {
    var opts = {
      endpoint: "/parent/upload/tus",
      retryDelays: [0, 1000, 3000, 5000, 10000, 20000],
      chunkSize: 5 * 1024 * 1024,
      removeFingerprintOnSuccess: true,
      metadata: buildMetadata(file, channelSelect.value, titleInput.value),
      onShouldRetry: shouldRetry,
      onError: function (err) {
        var msg = (err && err.message) ? err.message : String(err);
        var body = (err && err.originalResponse && err.originalResponse.getBody) ?
          err.originalResponse.getBody() : "";
        setStatus("Upload failed: " + (body || msg));
        reset();
      },
      onProgress: function (bytesUploaded, bytesTotal) {
        retryCount = 0;
        var pct = Math.floor((bytesUploaded / bytesTotal) * 100);
        bar.value = pct;
        setStatus("Uploading: " + pct + "% (" +
                  humanSize(bytesUploaded) + " of " + humanSize(bytesTotal) + ")");
      },
      onSuccess: function () {
        setStatus("Upload complete. Validating and processing…");
        var url = upload.url || "";
        var parts = url.split("/");
        var token = parts[parts.length - 1];
        var form = document.createElement("form");
        form.method = "POST";
        form.action = "/parent/upload/finalize/" + encodeURIComponent(token);
        document.body.appendChild(form);
        // The bytes are in: leaving for the finalize page must not trip
        // the beforeunload guard below.
        currentUpload = null;
        form.submit();
      },
    };
    var upload = new tus.Upload(file, opts);
    if (resumeFrom) {
      upload.resumeFromPreviousUpload(resumeFrom);
    }
    return upload;
  }

  function showResumeBanner(prev) {
    pendingResume = prev;
    var name = (prev.metadata && prev.metadata.filename) || "(unknown file)";
    resumeNameEl.textContent = name;
    // The real offset is only known once the file is picked again.
    resumePctEl.textContent = "0";
    resumeBanner.hidden = false;
  }

  function hideResumeBanner() {
    resumeBanner.hidden = true;
    pendingResume = null;
  }

  // Offer to resume the most recent unfinished upload.
  if (typeof tus !== "undefined" && tus.isSupported && window.localStorage) {
    try {
      var probe = new tus.Upload(new Blob([]), { endpoint: "/parent/upload/tus" });
      if (typeof probe.findPreviousUploads === "function") {
        var p = probe.findPreviousUploads();
        if (p && typeof p.then === "function") {
          p.then(function (uploads) {
            if (uploads && uploads.length) {
              showResumeBanner(uploads[uploads.length - 1]);
            }
          }).catch(function () {});
        }
      }
    } catch (e) { /* no resume support in this browser */ }
  }

  // iOS Safari transcodes a picked video before handing it over, which
  // can take a while; show that something is happening.
  fileInput.addEventListener("change", function () {
    var file = fileInput.files && fileInput.files[0];
    if (!file) return;
    progressWrap.hidden = false;
    bar.value = 0;
    setStatus("Preparing " + file.name + " (" + humanSize(file.size) + ")…");
  });

  resumeDismissBtn.addEventListener("click", function () {
    hideResumeBanner();
  });

  startBtn.addEventListener("click", function () {
    var file = fileInput.files && fileInput.files[0];
    if (!file) {
      setStatus("Pick a file first.");
      progressWrap.hidden = false;
      return;
    }
    if (file.size > MAX_BYTES) {
      setStatus("File is larger than the configured maximum. Lower the file size or raise the limit in Settings.");
      progressWrap.hidden = false;
      return;
    }

    progressWrap.hidden = false;
    startBtn.hidden = true;
    cancelBtn.hidden = false;
    bar.value = 0;
    retryCount = 0;

    // Resume only when the picked file matches the unfinished one.
    var resumeFrom = null;
    if (pendingResume && pendingResume.metadata &&
        pendingResume.metadata.filename === file.name &&
        pendingResume.size === file.size) {
      resumeFrom = pendingResume;
      setStatus("Resuming " + file.name + "…");
    } else {
      setStatus("Starting upload…");
    }

    var upload = buildUpload(file, resumeFrom);
    currentUpload = upload;
    hideResumeBanner();
    upload.start();
  });

  cancelBtn.addEventListener("click", function () {
    if (currentUpload) {
      currentUpload.abort(true);
      setStatus("Upload cancelled.");
      bar.value = 0;
    }
    reset();
  });

  window.addEventListener("beforeunload", function (e) {
    if (currentUpload) {
      e.preventDefault();
      e.returnValue = "An upload is in progress. Leaving will stop it.";
      return e.returnValue;
    }
  });
})();
