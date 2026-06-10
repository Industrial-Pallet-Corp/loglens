// Clicking an alternate suggestion fills the resolved-location input.
document.addEventListener("click", function (event) {
  const btn = event.target.closest("button.alt");
  if (!btn) return;
  const input = document.getElementsByName(btn.dataset.target)[0];
  if (input) {
    input.value = btn.dataset.value;
    input.classList.remove("unresolved");
    input.focus();
  }
});
