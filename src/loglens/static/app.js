// Mark a field "corrected" (light green) while its value differs from what was
// loaded; reverting the edit restores the original confidence shading.
function refreshCorrected(input) {
  if (input.value !== input.defaultValue) {
    if (input.dataset.origClass === undefined) {
      input.dataset.origClass = input.className;
      input.dataset.origTitle = input.title || "";
    }
    input.classList.remove("conf-low", "conf-mid", "unresolved");
    input.classList.add("conf-corrected");
    input.title = "Corrected - saved when you press Save corrections";
  } else if (input.dataset.origClass !== undefined) {
    input.className = input.dataset.origClass;
    input.title = input.dataset.origTitle;
    delete input.dataset.origClass;
    delete input.dataset.origTitle;
  }
}

document.addEventListener("input", function (event) {
  const input = event.target;
  if (input.matches && input.matches("form.sheet-form input")) {
    refreshCorrected(input);
  }
});

// Clicking an alternate suggestion fills the target (resolved-location) input.
document.addEventListener("click", function (event) {
  const btn = event.target.closest("button.alt");
  if (!btn) return;
  const input = document.getElementsByName(btn.dataset.target)[0];
  if (input) {
    input.value = btn.dataset.value;
    refreshCorrected(input);
    input.focus();
  }
});

// Inline save: submit sheet corrections via fetch and show a confirmation,
// preserving scroll position. Falls back to a normal POST when JS is off.
document.addEventListener("submit", async function (event) {
  const form = event.target;
  if (!form.classList.contains("sheet-form")) return;
  event.preventDefault();
  const status = form.querySelector(".save-status");
  const btn = form.querySelector('button[type="submit"]');
  if (btn) btn.disabled = true;
  if (status) status.textContent = "Saving...";
  try {
    const resp = await fetch(form.action, {
      method: "POST",
      headers: { "X-Inline": "1" },
      body: new FormData(form),
    });
    if (!resp.ok) throw new Error(resp.statusText);
    if (status) status.innerHTML = '<span class="saved">Saved \u2713</span>';
    // Corrections are committed: clear the green shading and make the saved
    // values the new baseline (user-confirmed fields are no longer shaded).
    form.querySelectorAll("input").forEach(function (input) {
      input.defaultValue = input.value;
      if (input.classList.contains("conf-corrected")) {
        input.classList.remove("conf-corrected");
        input.removeAttribute("title");
        delete input.dataset.origClass;
        delete input.dataset.origTitle;
      }
    });
  } catch (e) {
    if (status) status.innerHTML = '<span class="save-error">Save failed</span>';
  } finally {
    if (btn) btn.disabled = false;
  }
});

// Poll job status while pages are still being processed; reload when finished
// or whenever another page completes, so finished sheets become editable.
(function () {
  const el = document.querySelector("[data-poll]");
  if (!el) return;
  const url = el.getAttribute("data-poll");
  const bar = document.querySelector("[data-progress-bar]");
  const text = document.querySelector("[data-progress-text]");
  let lastDone = -1;

  async function tick() {
    let data;
    try {
      const resp = await fetch(url, { headers: { Accept: "application/json" } });
      data = await resp.json();
    } catch (e) {
      return setTimeout(tick, 3000);
    }
    const total = data.total || data.page_count || 0;
    const done = data.done + data.errored;
    if (bar && total) {
      bar.style.width = Math.round((done / total) * 100) + "%";
    }
    if (text) {
      text.textContent = `${data.done} of ${total} pages done` +
        (data.errored ? `, ${data.errored} failed` : "");
    }
    if (data.finished) {
      return window.location.reload();
    }
    if (done !== lastDone && lastDone !== -1) {
      return window.location.reload(); // a page finished: reveal its form
    }
    lastDone = done;
    setTimeout(tick, 2000);
  }
  tick();
})();
