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

// Job page: poll while pages are still being processed, swapping each sheet's
// card in place when its status changes. Settled sheets are never touched, so
// in-progress edits (focus, green corrected fields) survive background work.
(function () {
  const el = document.querySelector("[data-poll]");
  if (!el) return;
  const url = el.getAttribute("data-poll");
  const base = url.replace(/\/status$/, "");
  const bar = document.querySelector("[data-progress-bar]");
  const text = document.querySelector("[data-progress-text]");
  const badge = document.querySelector("[data-job-status]");

  const known = {};
  document.querySelectorAll("section.sheet[data-page]").forEach(function (s) {
    known[s.dataset.page] = s.dataset.sheetStatus;
  });

  async function swapSheet(page) {
    try {
      const resp = await fetch(base + "/sheets/" + page + "/html");
      if (!resp.ok) return;
      const html = await resp.text();
      const current = document.getElementById("sheet-" + page);
      if (current) current.outerHTML = html;
    } catch (e) {
      /* transient; the next poll retries the swap via status change */
    }
  }

  function finish() {
    const progress = document.querySelector("[data-progress]");
    if (progress) progress.remove();
    if (text) text.remove();
    const cancel = document.querySelector("[data-cancel-job]");
    if (cancel) cancel.remove();
  }

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
      text.textContent =
        data.job_status === "queued"
          ? "Waiting for an earlier job to finish..."
          : data.job_status === "cancelling"
            ? "Cancelling..."
            : `${data.done} of ${total} pages done` +
              (data.errored ? `, ${data.errored} failed` : "");
    }
    if (badge) {
      badge.textContent = data.job_status;
      badge.className = "badge badge-" + data.job_status;
    }
    for (const s of data.sheets || []) {
      const page = String(s.page_index);
      if (known[page] !== undefined && known[page] !== s.status) {
        known[page] = s.status;
        swapSheet(page);
      }
    }
    if (data.finished) return finish();
    setTimeout(tick, 2000);
  }
  tick();
})();

// Jobs list: poll live progress for queued/processing jobs, updating the
// status text in place; when a watched job finishes, refresh the list (the
// page holds no edit state, so a reload here is safe).
(function () {
  const rows = Array.from(document.querySelectorAll("[data-job-row]"));
  if (!rows.length) return;
  const ACTIVE = ["pending", "queued", "processing", "cancelling"];
  const watched = rows
    .filter(function (r) { return ACTIVE.includes(r.dataset.status); })
    .map(function (r) { return r.dataset.jobRow; });
  if (!watched.length) return;

  async function tick() {
    let data;
    try {
      const resp = await fetch("/status/active", { headers: { Accept: "application/json" } });
      data = await resp.json();
    } catch (e) {
      return setTimeout(tick, 4000);
    }
    const active = {};
    (data.jobs || []).forEach(function (j) { active[j.id] = j; });
    if (watched.some(function (id) { return !active[id]; })) {
      return window.location.reload(); // a job finished or was removed
    }
    for (const id of watched) {
      const j = active[id];
      const row = document.querySelector('[data-job-row="' + id + '"]');
      if (!row) continue;
      const badge = row.querySelector(".badge");
      if (badge) {
        badge.textContent = j.status;
        badge.className = "badge badge-" + j.status;
      }
      const progress = row.querySelector(".job-progress");
      if (progress) {
        progress.textContent =
          j.status === "queued"
            ? "Waiting..."
            : j.status === "cancelling"
              ? "Cancelling..."
              : "Processing... (" + j.done + "/" + j.total + " pages)";
      }
    }
    setTimeout(tick, 2000);
  }
  setTimeout(tick, 2000);
})();
