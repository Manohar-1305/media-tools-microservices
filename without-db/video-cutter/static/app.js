const $ = (id) => document.getElementById(id);

const state = {
  file: null,
  duration: 0,
  rotation: 0,
  flipH: false,
  flipV: false,
  reverse: false,
  image: null,
  previewUrl: null,
};

const video = $("preview");
const upload = $("video-file");
const workspace = $("workspace");
const uploadArea = $("upload-area");
const status = $("status");

function formatTime(seconds) {
  if (!Number.isFinite(seconds)) seconds = 0;
  const minutes = Math.floor(seconds / 60);
  const remainder = (seconds % 60).toFixed(1).padStart(4, "0");
  return `${String(minutes).padStart(2, "0")}:${remainder}`;
}

function setStatus(message) {
  status.textContent = message;
}

function currentStart() {
  return Number($("start").value || 0);
}

function currentEnd() {
  return Number($("end").value || state.duration);
}

function updateTimeLabels() {
  $("time-current").textContent = formatTime(video.currentTime);
  $("time-total").textContent = formatTime(state.duration);
  $("duration-label").textContent = formatTime(state.duration);

  const percent = state.duration
    ? (video.currentTime / state.duration) * 100
    : 0;

  $("playhead").style.left = `${percent}%`;
}

function updatePreviewTransform() {
  const transforms = [];

  if (state.rotation) transforms.push(`rotate(${state.rotation}deg)`);
  if (state.flipH) transforms.push("scaleX(-1)");
  if (state.flipV) transforms.push("scaleY(-1)");

  video.style.transform = transforms.join(" ") || "none";
}

function resetSettings() {
  $("start").value = 0;
  $("end").value = state.duration;
  $("start-range").value = 0;
  $("end-range").value = state.duration;

  $("crop-w").value = 0;
  $("crop-h").value = 0;
  $("crop-x").value = 0;
  $("crop-y").value = 0;

  $("filter").value = "none";
  $("volume").value = 100;
  $("volume-value").textContent = "100%";
  $("speed").value = 1;
  $("stabilize").checked = false;
  $("overlay-text").value = "";
  $("overlay-image").value = "";
  $("remove-bg").checked = false;
  $("bg-color").value = "#00ff00";

  state.rotation = 0;
  state.flipH = false;
  state.flipV = false;
  state.reverse = false;
  state.image = null;

  updatePreviewTransform();
  video.playbackRate = 1;
  video.currentTime = 0;
}

function buildTimeline() {
  const timeline = $("timeline");
  timeline.querySelectorAll("img").forEach((img) => img.remove());

  const count = 10;
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");

  canvas.width = 160;
  canvas.height = 80;

  let index = 0;

  function captureFrame() {
    if (index >= count) {
      video.currentTime = 0;
      updateTimeLabels();
      return;
    }

    const target = (state.duration * index) / count;
    video.currentTime = target;
  }

  function saveFrame() {
    try {
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const img = document.createElement("img");
      img.alt = `Video frame ${index + 1}`;
      img.src = canvas.toDataURL("image/jpeg", 0.65);
      img.draggable = false;
      timeline.insertBefore(img, $("playhead"));
    } catch (_) {
      // Thumbnail generation can fail for unsupported browser codecs.
    }

    index++;
    captureFrame();
  }

  const onSeeked = () => {
    saveFrame();
  };

  video.addEventListener("seeked", onSeeked);
  captureFrame();

  const cleanup = () => video.removeEventListener("seeked", onSeeked);
  setTimeout(cleanup, Math.max(5000, state.duration * 300));
}

upload.addEventListener("change", () => {
  const file = upload.files[0];
  if (!file) return;

  if (!file.type.startsWith("video/")) {
    setStatus("Select a valid video file.");
    return;
  }

  state.file = file;

  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = URL.createObjectURL(file);

  video.src = state.previewUrl;
  $("filename").textContent = file.name;
  uploadArea.hidden = true;
  workspace.hidden = false;
  setStatus("Loading video...");
});

video.addEventListener("loadedmetadata", () => {
  state.duration = video.duration || 0;

  $("start").value = 0;
  $("end").value = state.duration;
  $("start-range").max = state.duration;
  $("end-range").max = state.duration;
  $("start-range").value = 0;
  $("end-range").value = state.duration;

  updateTimeLabels();
  setStatus("Video ready");
  buildTimeline();
});

video.addEventListener("timeupdate", updateTimeLabels);

$("start").addEventListener("change", () => {
  let value = Math.max(0, Number($("start").value || 0));
  value = Math.min(value, currentEnd());
  $("start").value = value;
  $("start-range").value = value;
});

$("end").addEventListener("change", () => {
  let value = Math.min(
    state.duration,
    Number($("end").value || state.duration),
  );
  value = Math.max(value, currentStart());
  $("end").value = value;
  $("end-range").value = value;
});

$("start-range").addEventListener("input", () => {
  const value = Number($("start-range").value);
  $("start").value = Math.min(value, currentEnd());
  video.currentTime = Number($("start").value);
});

$("end-range").addEventListener("input", () => {
  const value = Number($("end-range").value);
  $("end").value = Math.max(value, currentStart());
});

document.querySelectorAll("[data-panel]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-panel]").forEach((item) => {
      item.classList.toggle("active", item === button);
    });

    document.querySelectorAll("[data-section]").forEach((panel) => {
      panel.hidden = panel.dataset.section !== button.dataset.panel;
    });
  });
});

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => {
    switch (button.dataset.action) {
      case "rotate-left":
        state.rotation = (state.rotation + 270) % 360;
        break;
      case "rotate-right":
        state.rotation = (state.rotation + 90) % 360;
        break;
      case "flip-h":
        state.flipH = !state.flipH;
        break;
      case "flip-v":
        state.flipV = !state.flipV;
        break;
      case "reverse":
        state.reverse = !state.reverse;
        button.classList.toggle("active", state.reverse);
        break;
    }

    updatePreviewTransform();
  });
});

$("volume").addEventListener("input", () => {
  $("volume-value").textContent = `${$("volume").value}%`;
});

$("speed").addEventListener("change", () => {
  video.playbackRate = Number($("speed").value);
});

$("overlay-image").addEventListener("change", () => {
  state.image = $("overlay-image").files[0] || null;
});

$("apply-crop").addEventListener("click", () => {
  const width = Number($("crop-w").value);
  const height = Number($("crop-h").value);

  if (width <= 0 || height <= 0) {
    video.style.clipPath = "none";
    return;
  }

  video.style.clipPath = `inset(0)`;
});

$("reset").addEventListener("click", () => {
  if (!state.file) return;
  resetSettings();
  setStatus("Changes reset");
});

$("close").addEventListener("click", () => {
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);

  video.pause();
  video.removeAttribute("src");
  video.load();

  state.file = null;
  state.previewUrl = null;
  upload.value = "";

  workspace.hidden = true;
  uploadArea.hidden = false;
  $("filename").textContent = "Choose a video to begin";
  setStatus("Ready");
});

$("export").addEventListener("click", async () => {
  if (!state.file) {
    setStatus("Select a video first.");
    return;
  }

  const start = currentStart();
  const end = currentEnd();

  if (end <= start) {
    setStatus("End time must be greater than start time.");
    return;
  }

  const form = new FormData();
  form.append("file", state.file);
  form.append("start", start);
  form.append("end", end);
  form.append("rotation", state.rotation);
  form.append("flip_h", String(state.flipH));
  form.append("flip_v", String(state.flipV));
  form.append("reverse", String(state.reverse));
  form.append("filter", $("filter").value);
  form.append("volume", $("volume").value);
  form.append("speed", $("speed").value);
  form.append("stabilize", String($("stabilize").checked));
  form.append("remove_bg", String($("remove-bg").checked));
  form.append("bg_color", "0x" + $("bg-color").value.slice(1).toUpperCase());

  form.append("crop_w", $("crop-w").value);
  form.append("crop_h", $("crop-h").value);
  form.append("crop_x", $("crop-x").value);
  form.append("crop_y", $("crop-y").value);
  form.append("overlay_text", $("overlay-text").value);

  if (state.image) form.append("overlay_image", state.image);

  const button = $("export");
  button.disabled = true;
  setStatus("Processing video...");

  try {
    const response = await fetch("/video-cutter/cut", {
      method: "POST",
      body: form,
    });

    if (!response.ok) {
      let message = "Unable to export video";
      try {
        const data = await response.json();
        message = data.error || message;
      } catch (_) {}
      throw new Error(message);
    }

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "edited_video.mp4";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);

    setStatus("Export complete");
  } catch (error) {
    setStatus(error.message || "Export failed");
  } finally {
    button.disabled = false;
  }
});
