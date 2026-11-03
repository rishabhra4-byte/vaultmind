// Apply the saved theme before the first paint, so there's no flash. Dark unless the person
// chose light. A separate file (not inline) so the Content-Security-Policy can forbid inline scripts.
(function () {
  var t = null;
  try { t = localStorage.getItem("vaultmind.theme"); } catch (e) {}
  document.documentElement.dataset.theme = t === "light" ? "light" : "dark";
})();
