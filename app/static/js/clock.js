// Sends this device's time to the box, which has no battery-backed clock
// and may be behind after a power cut, and its time zone, which a
// parent's device reports once. Only loaded when the box asks for it.
(function () {
  var tz = '';
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) {}
  try {
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/clock');
    xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
    xhr.send('now=' + Date.now() + '&tz=' + encodeURIComponent(tz));
  } catch (e) {}
})();
