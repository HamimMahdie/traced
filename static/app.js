/**
 * Trace — Frontend Logic
 *
 * Handles:
 *  • Reference photo upload (drag & drop + file picker)
 *  • SSE-based scan progress
 *  • Results rendering with thumbnails
 *  • Full-image preview modal
 *  • Copy action
 */

// ── DOM refs ────────────────────────────────────────────────────────────────

const dropZone        = document.getElementById("drop-zone");
const refFileInput    = document.getElementById("ref-file-input");
const refGrid         = document.getElementById("ref-grid");
const refCountBadge   = document.getElementById("ref-count-badge");
const btnClearRefs    = document.getElementById("btn-clear-refs");
const refStatus       = document.getElementById("ref-status");

const scanDirInput    = document.getElementById("scan-dir-input");
const thresholdSlider = document.getElementById("threshold-slider");
const thresholdValue  = document.getElementById("threshold-value");
const btnScan         = document.getElementById("btn-scan");
const btnStopScan     = document.getElementById("btn-stop-scan");
const scanProgress    = document.getElementById("scan-progress");
const progressFill    = document.getElementById("progress-fill");
const progressText    = document.getElementById("progress-text");
const progressPercent = document.getElementById("progress-percent");
const scanStatus      = document.getElementById("scan-status");

const panelResults    = document.getElementById("panel-results");
const statMatches     = document.getElementById("stat-matches");
const statTotal       = document.getElementById("stat-total");
const statAccuracy    = document.getElementById("stat-accuracy");
const sampleGrid      = document.getElementById("sample-grid");

const outputDirInput  = document.getElementById("output-dir-input");
const btnCopy         = document.getElementById("btn-copy");
const copyStatus      = document.getElementById("copy-status");

// ── State ───────────────────────────────────────────────────────────────────

let refCount = 0;
let sseController = null;  // AbortController for stopping SSE
let matchedFiles = [];     // Populated during scan
let previewIndex = -1;     // Current index in matchedFiles for preview

// ── Utility ─────────────────────────────────────────────────────────────────

async function parseJsonResponse(resp) {
  const contentType = resp.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return await resp.json();
  }
  const text = await resp.text();
  throw new Error(text.slice(0, 150) || `Server returned ${resp.status}`);
}

function showMsg(el, text, type = "info") {
  el.textContent = text;
  el.className = `status-msg visible ${type}`;
}

function hideMsg(el) {
  el.className = "status-msg";
}

function updateScanButton() {
  btnScan.disabled = refCount === 0;
}

// ── Reference Upload ────────────────────────────────────────────────────────

dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("dragover");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("dragover");
});

dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover");
  if (e.dataTransfer.files.length) {
    uploadReferenceFiles(e.dataTransfer.files);
  }
});

refFileInput.addEventListener("change", () => {
  if (refFileInput.files.length) {
    uploadReferenceFiles(refFileInput.files);
    refFileInput.value = "";
  }
});

async function uploadReferenceFiles(files) {
  hideMsg(refStatus);
  const formData = new FormData();
  for (const f of files) {
    formData.append("files", f);
  }

  try {
    const resp = await fetch("/api/upload-reference", { method: "POST", body: formData });
    const data = await parseJsonResponse(resp);

    if (!resp.ok) {
      showMsg(refStatus, data.error || "Upload failed.", "error");
      return;
    }

    if (data.faces_added === 0) {
      showMsg(refStatus, "No faces detected in the uploaded image(s). Try a clearer photo.", "warning");
      return;
    }

    showMsg(refStatus, `${data.faces_added} face(s) added. Total references: ${data.total_references}`, "success");

    // Refresh the reference grid
    await loadReferences();
  } catch (err) {
    showMsg(refStatus, `Upload error: ${err.message}`, "error");
  }
}

async function loadReferences() {
  try {
    const resp = await fetch("/api/references");
    const data = await parseJsonResponse(resp);

    refCount = data.count;
    refCountBadge.textContent = refCount;
    btnClearRefs.disabled = refCount === 0;
    updateScanButton();

    refGrid.innerHTML = "";
    data.crops.forEach((b64, idx) => {
      const div = document.createElement("div");
      div.className = "ref-face fade-in";
      div.innerHTML = `
        <img src="data:image/jpeg;base64,${b64}" alt="Reference face ${idx + 1}" />
        <button class="remove-btn" data-idx="${idx}" title="Remove">✕</button>
      `;
      refGrid.appendChild(div);
    });

    // Attach remove handlers
    refGrid.querySelectorAll(".remove-btn").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const idx = btn.getAttribute("data-idx");
        await fetch(`/api/references/${idx}`, { method: "DELETE" });
        await loadReferences();
      });
    });
  } catch (err) {
    console.error("Failed to load references:", err);
  }
}

btnClearRefs.addEventListener("click", async () => {
  await fetch("/api/references", { method: "DELETE" });
  await loadReferences();
  hideMsg(refStatus);
});

// ── Threshold Slider ────────────────────────────────────────────────────────

thresholdSlider.addEventListener("input", () => {
  thresholdValue.textContent = parseFloat(thresholdSlider.value).toFixed(2);
});

// ── Scan ────────────────────────────────────────────────────────────────────

scanDirInput.addEventListener("input", () => {
  const dir = scanDirInput.value.trim();
  if (dir) {
    outputDirInput.placeholder = `Default: ${dir.replace(/\/+$/, "")}/Traced`;
  } else {
    outputDirInput.placeholder = "Leave blank for default: [scan dir]/Traced";
  }
});

btnScan.addEventListener("click", startScan);

async function startScan() {
  const directory = scanDirInput.value.trim();
  const tolerance = parseFloat(thresholdSlider.value);

  if (!directory) {
    showMsg(scanStatus, "Please enter a directory path.", "warning");
    return;
  }

  outputDirInput.placeholder = `Default: ${directory.replace(/\/+$/, "")}/Traced`;

  // Reset UI
  hideMsg(scanStatus);
  matchedFiles = [];
  sampleGrid.innerHTML = "";
  panelResults.style.display = "none";

  // Show progress
  scanProgress.classList.add("active");
  progressFill.style.width = "0%";
  progressText.textContent = "Starting scan...";
  progressPercent.textContent = "0%";

  btnScan.disabled = true;
  btnStopScan.style.display = "inline-flex";

  try {
    sseController = new AbortController();

    const resp = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ directory, tolerance }),
      signal: sseController.signal,
    });

    if (!resp.ok) {
      let errMsg = "Scan failed.";
      try {
        const errData = await parseJsonResponse(resp);
        errMsg = errData.error || errMsg;
      } catch (e) {
        errMsg = e.message;
      }
      showMsg(scanStatus, errMsg, "error");
      resetScanUI();
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Parse SSE events from buffer
      const lines = buffer.split("\n");
      buffer = lines.pop(); // keep incomplete line

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const event = JSON.parse(line.slice(6));
            handleScanEvent(event);
          } catch (e) {
            // ignore malformed JSON lines
          }
        }
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") {
      showMsg(scanStatus, `Scan error: ${err.message}`, "error");
    }
  } finally {
    resetScanUI();
  }
}

function handleScanEvent(event) {
  switch (event.type) {
    case "progress":
      {
        const pct = event.total > 0 ? Math.round((event.current / event.total) * 100) : 0;
        progressFill.style.width = pct + "%";
        progressPercent.textContent = pct + "%";
        progressText.textContent = event.message;

        // Update live stats
        statMatches.textContent = event.matches ?? 0;
        statTotal.textContent = event.current;
      }
      break;

    case "match":
      matchedFiles.push({ file: event.file, similarity: event.similarity });

      // Add sample thumbnail (show first 20)
      if (matchedFiles.length <= 20) {
        addSampleThumbnail(event.file, event.similarity, matchedFiles.length - 1);
      }

      // Show results panel as soon as first match arrives
      panelResults.style.display = "block";
      break;

    case "done":
      progressFill.style.width = "100%";
      progressPercent.textContent = "100%";
      showMsg(scanStatus, event.message, "success");

      // Final stats
      statMatches.textContent = event.matches;
      statTotal.textContent = event.total;
      if (event.total > 0) {
        statAccuracy.textContent = ((event.matches / event.total) * 100).toFixed(1) + "%";
      }

      panelResults.style.display = "block";
      break;

    case "error":
      console.warn("Scan error:", event.message);
      break;
  }
}

function addSampleThumbnail(filepath, similarity, index) {
  const div = document.createElement("div");
  div.className = "sample-item fade-in";
  div.dataset.matchIndex = index;

  const filename = filepath.split("/").pop();
  const pct = (similarity * 100).toFixed(1);

  div.innerHTML = `
    <img src="/api/thumbnail?path=${encodeURIComponent(filepath)}" alt="${filename}" loading="lazy" />
    <div class="sample-info">${filename}</div>
    <div class="sample-info sample-distance">Similarity: ${pct}%</div>
  `;

  div.addEventListener("click", () => openPreview(index));
  sampleGrid.appendChild(div);
}

function resetScanUI() {
  btnScan.disabled = refCount === 0;
  btnStopScan.style.display = "none";
  sseController = null;

  // Keep progress visible if scan completed, hide after a delay
  setTimeout(() => {
    scanProgress.classList.remove("active");
  }, 3000);
}

btnStopScan.addEventListener("click", () => {
  if (sseController) {
    sseController.abort();
    showMsg(scanStatus, "Scan stopped by user.", "warning");
  }
});

// ── Copy ────────────────────────────────────────────────────────────────────

btnCopy.addEventListener("click", async () => {
  let outputDir = outputDirInput.value.trim();
  if (!outputDir) {
    const scanDir = scanDirInput.value.trim();
    if (scanDir) {
      outputDir = scanDir.replace(/\/+$/, "") + "/Traced";
    }
  }

  if (matchedFiles.length === 0) {
    showMsg(copyStatus, "No matched files to copy. Run a scan first.", "warning");
    return;
  }

  btnCopy.disabled = true;
  hideMsg(copyStatus);

  try {
    const resp = await fetch("/api/copy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ output_directory: outputDir }),
    });

    const data = await parseJsonResponse(resp);

    if (!resp.ok) {
      showMsg(copyStatus, data.error || "Copy failed.", "error");
      return;
    }

    if (data.output_directory) {
      outputDirInput.value = data.output_directory;
    }

    showMsg(copyStatus, data.message, "success");

    if (data.errors && data.errors.length > 0) {
      console.warn("Copy errors:", data.errors);
    }
  } catch (err) {
    showMsg(copyStatus, `Copy error: ${err.message}`, "error");
  } finally {
    btnCopy.disabled = false;
  }
});

// ── Init ────────────────────────────────────────────────────────────────────

loadReferences();


// ── Image Preview Modal ──────────────────────────────────────────────────────

const previewOverlay = document.getElementById("preview-overlay");
const previewImg     = document.getElementById("preview-img");
const previewCaption = document.getElementById("preview-caption");
const previewClose   = document.getElementById("preview-close");
const previewPrev    = document.getElementById("preview-prev");
const previewNext    = document.getElementById("preview-next");

function openPreview(index) {
  if (index < 0 || index >= matchedFiles.length) return;
  previewIndex = index;

  const match = matchedFiles[previewIndex];
  const filename = match.file.split("/").pop();
  const pct = (match.similarity * 100).toFixed(1);

  // Use the full-resolution image endpoint
  previewImg.src = `/api/image?path=${encodeURIComponent(match.file)}`;
  previewCaption.textContent = `${filename}  —  Similarity: ${pct}%  —  ${previewIndex + 1} of ${matchedFiles.length}`;

  previewOverlay.classList.add("active");
  document.body.style.overflow = "hidden";

  // Update nav button visibility
  previewPrev.style.display = previewIndex > 0 ? "flex" : "none";
  previewNext.style.display = previewIndex < matchedFiles.length - 1 ? "flex" : "none";
}

function closePreview() {
  previewOverlay.classList.remove("active");
  document.body.style.overflow = "";
  previewImg.src = "";
  previewIndex = -1;
}

previewClose.addEventListener("click", closePreview);

previewOverlay.addEventListener("click", (e) => {
  // Close when clicking the backdrop (not the image or buttons)
  if (e.target === previewOverlay) {
    closePreview();
  }
});

previewPrev.addEventListener("click", (e) => {
  e.stopPropagation();
  openPreview(previewIndex - 1);
});

previewNext.addEventListener("click", (e) => {
  e.stopPropagation();
  openPreview(previewIndex + 1);
});

// Keyboard navigation
document.addEventListener("keydown", (e) => {
  if (previewIndex < 0) return; // modal not open

  switch (e.key) {
    case "Escape":
      closePreview();
      break;
    case "ArrowLeft":
      openPreview(previewIndex - 1);
      break;
    case "ArrowRight":
      openPreview(previewIndex + 1);
      break;
  }
});
