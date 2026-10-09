// Follow import jobs live: poll the status endpoint while anything is
// waiting or running, and redraw the list. Text is set with textContent
// only, never parsed as HTML.
(function () {
  "use strict";
  var box = document.getElementById("import-jobs");
  if (!box) { return; }
  var url = box.getAttribute("data-status-url");
  var csrf = box.getAttribute("data-csrf");
  var cancelAll = document.getElementById("import-cancel-all");
  var clear = document.getElementById("import-clear");
  var timer = null;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text) { node.textContent = text; }
    return node;
  }

  function cancelForm(job) {
    var form = el("form", "import-cancel");
    form.method = "POST";
    form.action = "/parent/import/jobs/" + job.id + "/cancel";
    form.setAttribute("data-confirm", "Cancel this import? The file on the drive is not touched.");
    var token = el("input");
    token.type = "hidden";
    token.name = "csrf_token";
    token.value = csrf;
    var button = el("button", "btn btn-sm btn-secondary", "Cancel");
    button.type = "submit";
    form.appendChild(token);
    form.appendChild(button);
    return form;
  }

  function row(job) {
    var item = el("div", "import-job import-" + job.state);
    item.setAttribute("data-id", job.id);
    if (job.stalled) { item.className += " import-stalled"; }
    var head = el("div", "import-job-head");
    head.appendChild(el("strong", "import-job-title", job.title));
    head.appendChild(document.createTextNode(" "));
    head.appendChild(el("span", "muted fs-13 import-job-name", job.name));
    item.appendChild(head);
    var line = el("div", "import-job-line");
    line.appendChild(el("span", "import-job-text", job.line));
    line.appendChild(document.createTextNode(" "));
    line.appendChild(el("span", "muted import-job-details", job.details));
    item.appendChild(line);
    if (job.active) {
      var bar = el("progress", "import-bar");
      bar.max = 100;
      if (job.percent !== null) { bar.value = job.percent; }
      item.appendChild(bar);
      item.appendChild(cancelForm(job));
    } else if (job.state === "done" && job.video_id) {
      var link = el("a", "fs-13", "Open in Content");
      link.href = "/parent/content?highlight=" + encodeURIComponent(job.video_id);
      item.appendChild(link);
    }
    return item;
  }

  function draw(data) {
    while (box.firstChild) { box.removeChild(box.firstChild); }
    if (!data.jobs.length) {
      box.appendChild(el("p", "muted import-empty", "Nothing imported yet."));
    }
    data.jobs.forEach(function (job) { box.appendChild(row(job)); });
    if (cancelAll) { cancelAll.hidden = !data.active; }
    if (clear) { clear.hidden = data.jobs.length === data.active; }
  }

  function poll() {
    fetch(url, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) { return; }
        draw(data);
        schedule(data.active ? 2000 : 15000);
      })
      .catch(function () { schedule(5000); });
  }

  function schedule(ms) {
    if (timer) { clearTimeout(timer); }
    timer = setTimeout(poll, ms);
  }

  schedule(box.querySelector(".import-queued, .import-working, .import-cancelling") ? 1000 : 15000);
})();
