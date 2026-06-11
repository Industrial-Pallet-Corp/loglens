// Mark a field "corrected" (light green): it will be committed on save.
function markCorrected(input, title) {
  if (input.dataset.origClass === undefined) {
    input.dataset.origClass = input.className;
    input.dataset.origTitle = input.title || "";
  }
  input.classList.remove("conf-low", "conf-mid", "unresolved");
  input.classList.add("conf-corrected");
  input.title = title;
}

// Shade a field while its value differs from what was loaded; reverting the
// edit restores the original confidence shading (unless it was touch-confirmed).
function refreshCorrected(input) {
  if (input.value !== input.defaultValue) {
    markCorrected(input, "Corrected - saved when you press Save corrections");
  } else if (input.dataset.confirmed === "1") {
    // Touch-confirmed at its original value: stays green.
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

// Confirm-by-touch: focusing a shaded (low/mid confidence) field counts as the
// user verifying it, even with no edit. It turns green and is saved as a
// user-confirmed value on "Save corrections".
document.addEventListener("focusin", function (event) {
  const input = event.target;
  if (!(input.matches && input.matches("form.sheet-form input"))) return;
  if (!input.classList.contains("conf-low") && !input.classList.contains("conf-mid")) return;
  if (!input.value.trim()) return; // nothing to confirm in an empty field
  input.dataset.confirmed = "1";
  markCorrected(input, "Confirmed - saved when you press Save corrections");
});

// Tab / Shift+Tab cycles round and round through the colored fields (shaded
// yellow/red, or green pending corrections) of the card the focus is in,
// wrapping at either end. It never leaves the card: moving to another card
// takes a deliberate click, as does pressing Save corrections. Once the card
// is all white (corrections saved), Tab does nothing. Outside a card, native
// tabbing applies.
document.addEventListener("keydown", function (event) {
  if (event.key !== "Tab") return;
  const active = document.activeElement;
  const form = active && active.closest && active.closest("form.sheet-form");
  if (!form) return;
  event.preventDefault();
  const fields = Array.from(
    form.querySelectorAll("input.conf-low, input.conf-mid, input.conf-corrected")
  );
  if (!fields.length) return;
  let target;
  const idx = fields.indexOf(active);
  if (idx !== -1) {
    target = fields[(idx + (event.shiftKey ? -1 : 1) + fields.length) % fields.length];
  } else if (event.shiftKey) {
    // From an uncolored spot in the card: nearest colored field before it,
    // wrapping to the last.
    target = fields[fields.length - 1];
    for (let i = fields.length - 1; i >= 0; i--) {
      if (active.compareDocumentPosition(fields[i]) & Node.DOCUMENT_POSITION_PRECEDING) {
        target = fields[i];
        break;
      }
    }
  } else {
    // Nearest colored field after it, wrapping to the first.
    target = fields[0];
    for (const field of fields) {
      if (active.compareDocumentPosition(field) & Node.DOCUMENT_POSITION_FOLLOWING) {
        target = field;
        break;
      }
    }
  }
  target.focus();
  if (target.select) target.select();
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

// Inline save: submit only the corrected/confirmed (green) fields via fetch,
// preserving scroll position. Falls back to a normal full POST when JS is off.
document.addEventListener("submit", async function (event) {
  const form = event.target;
  if (!form.classList.contains("sheet-form")) return;
  event.preventDefault();
  const status = form.querySelector(".save-status");
  const corrected = form.querySelectorAll("input.conf-corrected");
  if (!corrected.length) {
    if (status) status.textContent = "Nothing to save";
    return;
  }
  const data = new FormData();
  corrected.forEach(function (input) {
    data.append(input.name, input.value);
  });
  data.append("_explicit", "1");
  const btn = form.querySelector('button[type="submit"]');
  if (btn) btn.disabled = true;
  if (status) status.textContent = "Saving...";
  try {
    const resp = await fetch(form.action, {
      method: "POST",
      headers: { "X-Inline": "1" },
      body: data,
    });
    if (!resp.ok) throw new Error(resp.statusText);
    if (status) status.innerHTML = '<span class="saved">Saved \u2713</span>';
    // Corrections are committed: clear the green shading and make the saved
    // values the new baseline (user-confirmed fields are no longer shaded).
    corrected.forEach(function (input) {
      input.defaultValue = input.value;
      input.classList.remove("conf-corrected");
      input.removeAttribute("title");
      delete input.dataset.origClass;
      delete input.dataset.origTitle;
      delete input.dataset.confirmed;
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
