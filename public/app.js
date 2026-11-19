const themeButtons = document.querySelectorAll("[data-theme-toggle]");

for (const button of themeButtons) {
  button.addEventListener("click", () => {
    const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nextTheme;
    try {
      localStorage.setItem("naymark-theme", nextTheme);
    } catch {
      // The selected theme still applies for this page view.
    }
  });
}

for (const button of document.querySelectorAll("[data-copy]")) {
  button.addEventListener("click", async () => {
    const code = document.getElementById(button.dataset.copy);
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code.textContent);
      button.textContent = "Copied";
      window.setTimeout(() => {
        button.textContent = "Copy";
      }, 1400);
    } catch {
      button.textContent = "Copy unavailable";
      window.setTimeout(() => {
        button.textContent = "Copy";
      }, 1600);
    }
  });
}

const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const chooseButton = document.getElementById("choose-image");
const clearButton = document.getElementById("clear-image");
const processButton = document.getElementById("process-image");
const downloadButton = document.getElementById("download-image");
const fileStatusText = document.getElementById("file-status-text");
const message = document.getElementById("playground-message");
const emptyState = document.getElementById("empty-state");
const beforeImage = document.getElementById("before-image");
const afterLayer = document.getElementById("after-layer");
const afterImage = document.getElementById("after-image");
const resultPlaceholder = document.getElementById("result-placeholder");
const compareLabels = document.getElementById("compare-labels");
const compareRange = document.getElementById("compare-range");
const compareDivider = document.getElementById("compare-divider");
const previewModes = document.getElementById("preview-modes");
const previewModeButtons = document.querySelectorAll("[data-preview-mode]");
const zoomOutButton = document.getElementById("zoom-out");
const zoomInButton = document.getElementById("zoom-in");
const zoomFitButton = document.getElementById("zoom-fit");
const zoomLevel = document.getElementById("zoom-level");

if (dropZone && fileInput && processButton && fileStatusText) {
  const maximumBytes = 4 * 1024 * 1024;
  let selectedFile = null;
  let originalUrl = null;
  let processedUrl = null;
  let processing = false;
  let zoom = 100;

  function revokePreview(url) {
    if (url) URL.revokeObjectURL(url);
  }

  function setMessage(text, isError = false) {
    message.textContent = text;
    message.classList.toggle("message-error", isError);
  }

  function setZoom(value) {
    zoom = Math.max(50, Math.min(200, value));
    const transform = `scale(${zoom / 100})`;
    beforeImage.style.transform = transform;
    afterImage.style.transform = transform;
    zoomLevel.textContent = `${zoom}%`;
    zoomOutButton.disabled = !selectedFile || zoom <= 50;
    zoomInButton.disabled = !selectedFile || zoom >= 200;
    zoomFitButton.disabled = !selectedFile || zoom === 100;
  }

  function setPreviewMode(mode) {
    const hasImage = Boolean(selectedFile);
    const hasResult = Boolean(processedUrl);
    const isCompare = hasResult && mode === "compare";
    const showResult = hasResult && mode !== "original";

    beforeImage.hidden = !hasImage || (hasResult && mode === "result");
    afterLayer.hidden = !showResult;
    if (showResult) {
      afterLayer.style.clipPath = isCompare
        ? `inset(0 0 0 ${compareRange.value}%)`
        : "inset(0)";
    }

    compareLabels.hidden = !isCompare;
    compareRange.hidden = !isCompare;
    compareDivider.hidden = !isCompare;
    if (isCompare) compareDivider.style.left = `${compareRange.value}%`;
    previewModes.hidden = !hasResult;
    previewModeButtons.forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.previewMode === mode));
    });
  }

  function clearPreviews() {
    revokePreview(originalUrl);
    revokePreview(processedUrl);
    originalUrl = null;
    processedUrl = null;
    beforeImage.removeAttribute("src");
    afterImage.removeAttribute("src");
    downloadButton.removeAttribute("href");
  }

  function clearSelection() {
    selectedFile = null;
    clearPreviews();
    fileInput.value = "";
    emptyState.hidden = false;
    resultPlaceholder.hidden = true;
    compareRange.value = "50";
    setPreviewMode("original");
    setZoom(100);
    clearButton.hidden = true;
    downloadButton.hidden = true;
    processButton.disabled = true;
    processButton.innerHTML = 'Remove watermark <span aria-hidden="true">↗</span>';
    fileStatusText.textContent = "No image selected";
    setMessage("");
  }

  function selectFile(file) {
    if (processing) return;
    if (!file.type.startsWith("image/")) {
      clearSelection();
      setMessage("Choose an image file.", true);
      return;
    }
    if (file.size > maximumBytes) {
      clearSelection();
      setMessage("This image is larger than the 4 MiB upload limit.", true);
      return;
    }

    clearPreviews();
    selectedFile = file;
    fileInput.value = "";
    originalUrl = URL.createObjectURL(file);
    beforeImage.src = originalUrl;
    emptyState.hidden = true;
    resultPlaceholder.hidden = false;
    compareRange.value = "50";
    setPreviewMode("original");
    setZoom(100);
    clearButton.hidden = false;
    downloadButton.hidden = true;
    processButton.disabled = false;
    fileStatusText.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
    setMessage("Image ready. Process it to preview the result.");
  }

  chooseButton.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    if (fileInput.files?.[0]) selectFile(fileInput.files[0]);
  });
  clearButton.addEventListener("click", clearSelection);

  previewModeButtons.forEach((button) => {
    button.addEventListener("click", () => setPreviewMode(button.dataset.previewMode));
  });
  zoomOutButton.addEventListener("click", () => setZoom(zoom - 25));
  zoomInButton.addEventListener("click", () => setZoom(zoom + 25));
  zoomFitButton.addEventListener("click", () => setZoom(100));

  dropZone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropZone.classList.add("is-dragging");
  });
  dropZone.addEventListener("dragleave", (event) => {
    if (!dropZone.contains(event.relatedTarget)) dropZone.classList.remove("is-dragging");
  });
  dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropZone.classList.remove("is-dragging");
    const file = event.dataTransfer?.files?.[0];
    if (file) selectFile(file);
  });
  dropZone.addEventListener("keydown", (event) => {
    if (event.target !== dropZone || !["Enter", " "].includes(event.key)) return;
    event.preventDefault();
    fileInput.click();
  });

  compareRange.addEventListener("input", () => {
    const value = Number(compareRange.value);
    afterLayer.style.clipPath = `inset(0 0 0 ${value}%)`;
    compareDivider.style.left = `${value}%`;
  });

  processButton.addEventListener("click", async () => {
    if (!selectedFile || processing) return;
    const file = selectedFile;
    const form = new FormData();
    form.append("file", file);
    processing = true;
    processButton.disabled = true;
    chooseButton.disabled = true;
    clearButton.disabled = true;
    fileInput.disabled = true;
    processButton.innerHTML = '<span class="button-spinner" aria-hidden="true"></span> Processing';
    setMessage("Sending image to the Naymark API...");

    try {
      const response = await fetch("/v1/process", { method: "POST", body: form });
      if (!response.ok) {
        let detail = `Request failed (${response.status}).`;
        try {
          const error = await response.json();
          detail = error.detail || error.error?.message || detail;
        } catch {
          // Use the status message when the server does not return JSON.
        }
        throw new Error(detail);
      }

      const result = await response.blob();
      if (processedUrl) URL.revokeObjectURL(processedUrl);
      processedUrl = URL.createObjectURL(result);
      afterImage.src = processedUrl;
      resultPlaceholder.hidden = true;
      downloadButton.href = processedUrl;
      downloadButton.download = `${file.name.replace(/\.[^.]+$/, "")}-processed.png`;
      downloadButton.hidden = false;
      setPreviewMode("compare");
      setMessage("Done. Drag the divider to compare your images.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not process this image.", true);
    } finally {
      processing = false;
      processButton.disabled = false;
      chooseButton.disabled = false;
      clearButton.disabled = false;
      fileInput.disabled = false;
      processButton.innerHTML = 'Process again <span aria-hidden="true">↗</span>';
    }
  });
}
