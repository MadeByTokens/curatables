/* Translated messages for the kid scripts (ES3). The page puts them on
   <body data-messages="{...}">, keyed by the English text. */
var kidT = (function () {
  var messages = null;
  function load() {
    messages = {};
    var raw = document.body && document.body.getAttribute('data-messages');
    if (raw && window.JSON) {
      try { messages = JSON.parse(raw); } catch (e) { messages = {}; }
    }
  }
  return function (text, values) {
    if (messages === null) { load(); }
    var out = messages[text] || text;
    if (values) {
      for (var key in values) {
        if (values.hasOwnProperty(key)) {
          out = out.split('{' + key + '}').join(String(values[key]));
        }
      }
    }
    return out;
  };
})();
