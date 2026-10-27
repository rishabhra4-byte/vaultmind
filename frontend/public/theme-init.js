// Apply the saved or system theme before the first paint, so there's no flash.
// A separate file (not inline) so the Content-Security-Policy can forbid inline scripts.
(function () {
  var t = null;
  try { t = localStorage.getItem("vaultmind.theme"); } catch (e) {}
  if (t !== "light" && t !== "dark") {
    t = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.dataset.theme = t;
})();
