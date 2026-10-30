(function () {
  const $ = (id) => document.getElementById(id);
  const form = $("compress-form"), input = $("image"), zone = $("dropzone");
  const submit = $("submit"), errorBox = $("error");
  const maxBytes = parseInt(form.dataset.maxBytes, 10), maxMb = form.dataset.maxMb;
  let objectUrl = null;

  const fmt = (n) => {
    const sign = n < 0 ? "-" : ""; n = Math.abs(n);
    if (n < 1024) return sign + n + " B";
    const units = ["KB", "MB", "GB"]; let i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < 2);
    return sign + n.toFixed(1) + " " + units[i];
  };
  const showError = (msg) => { errorBox.textContent = msg; errorBox.hidden = !msg; };

  function setFile(file) {
    showError("");
    if (!file) return;
    if (!file.type.startsWith("image/")) { showError("Please choose an image file."); input.value = ""; submit.disabled = true; return; }
    if (file.size > maxBytes) { showError("File is too large. The maximum size is " + maxMb + " MB."); input.value = ""; submit.disabled = true; return; }
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file);
    const img = $("preview-img");
    img.onload = () => { $("preview-meta").textContent = fmt(file.size) + " · " + img.naturalWidth + "×" + img.naturalHeight + " px"; };
    img.src = objectUrl;
    $("preview-name").textContent = file.name;
    $("preview").hidden = false;
    submit.disabled = false;
  }

  input.addEventListener("change", () => setFile(input.files[0]));
  zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("drag"); }));
  zone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; setFile(input.files[0]); }
  });
  $("quality").addEventListener("input", (e) => { $("quality-out").textContent = e.target.value; });

  function updateStats(stats) {
    const box = $("stats");
    if (!stats) return;
    box.querySelectorAll("[data-stat]").forEach((el) => {
      const k = el.dataset.stat;
      if (k === "total_images") el.textContent = stats.total_images;
      if (k === "total_saved") el.textContent = fmt(stats.total_saved_bytes);
      if (k === "avg") el.textContent = stats.average_reduction_percent + "%";
      if (k === "source") el.textContent = stats.cached ? "Served from Redis cache" : "Read from MongoDB";
    });
  }

  function setLoading(on) {
    submit.disabled = on || !input.files.length;
    submit.querySelector(".spinner").hidden = !on;
    submit.querySelector(".label").textContent = on ? "Compressing…" : "Compress image";
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!input.files.length) { showError("Please choose an image first."); return; }
    showError(""); setLoading(true);
    try {
      const res = await fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin" });
      let data = {};
      try { data = await res.json(); } catch (_) { /* non-JSON error page */ }
      if (!res.ok) { showError(data.error || "Compression failed (" + res.status + ")."); return; }
      $("empty").hidden = true; $("result-body").hidden = false;
      const msg = $("result-msg"); msg.textContent = data.message; msg.className = "alert alert-icon " + (data.smaller ? "alert-ok" : "alert-warn");
      $("result-img").src = data.preview_url;
      $("r-orig").textContent = fmt(data.original_size) + " (" + data.original_format + ")";
      $("r-comp").textContent = fmt(data.compressed_size);
      $("r-saved").textContent = fmt(data.saved_bytes) + " (" + data.reduction_percent + "%)";
      $("r-format").textContent = data.output_format;
      $("r-dims").textContent = data.width + "×" + data.height + " px";
      $("r-quality").textContent = data.quality;
      $("download").href = data.download_url;
      updateStats(data.stats);
    } catch (_) {
      showError("Network error. Please check your connection and try again.");
    } finally { setLoading(false); }
  });
})();
