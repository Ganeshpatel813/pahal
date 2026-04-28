/**
 * face-scanner.js  — Real face registration using face-api.js
 * Captures 128-dim neural descriptor (no random faking).
 */
const MODEL_URL = "/static/models";
let _regStream   = null;
let _modelsReady = false;

async function loadFaceModels() {
    if (_modelsReady) return true;
    try {
        await Promise.all([
            faceapi.nets.tinyFaceDetector.loadFromUri(MODEL_URL),
            faceapi.nets.faceLandmark68Net.loadFromUri(MODEL_URL),
            faceapi.nets.faceRecognitionNet.loadFromUri(MODEL_URL),
        ]);
        _modelsReady = true;
        return true;
    } catch (e) { console.error("[FaceAPI] Model load failed:", e); return false; }
}

function initFaceScanner(containerId, callback) {
    const wrap = document.getElementById(containerId);
    wrap.innerHTML = `
        <div class="relative bg-black rounded-xl overflow-hidden mx-auto" style="max-width:340px;height:260px">
            <video id="reg-vid" autoplay playsinline muted class="w-full h-full object-cover"></video>
            <div class="absolute inset-0 flex items-center justify-center pointer-events-none">
                <div id="reg-ring" class="w-44 h-44 rounded-full border-4 border-dashed border-white/40 flex items-center justify-center transition-all duration-500">
                    <p id="reg-ring-txt" class="text-white text-xs font-bold text-center opacity-70 px-4 leading-snug">Position your face<br>inside the circle</p>
                </div>
            </div>
            <canvas id="reg-canvas" class="hidden"></canvas>
        </div>
        <div id="reg-model-msg" class="mt-3 alert alert-info text-xs text-center">
            <i class="fas fa-spinner fa-spin mr-1"></i> Loading AI face recognition models…
        </div>
        <div id="reg-cam-err" class="hidden mt-2 alert alert-error text-xs"></div>
        <div class="mt-3 flex gap-2 justify-center">
            <button id="reg-start-btn" onclick="regStartCam()" disabled
                class="btn btn-primary btn-sm opacity-50">
                <i class="fas fa-camera"></i> Start Camera
            </button>
            <button id="reg-capture-btn" onclick="regCapture()"
                class="hidden btn btn-success btn-sm">
                <i class="fas fa-check"></i> Capture Face
            </button>
        </div>
        <p class="text-gray-400 text-xs text-center mt-2">Allow camera when prompted.</p>`;

    window._faceScanCB = callback;

    loadFaceModels().then(ok => {
        const msg = document.getElementById("reg-model-msg");
        const btn = document.getElementById("reg-start-btn");
        if (ok) {
            msg.className = "mt-3 alert alert-success text-xs text-center";
            msg.innerHTML = "<i class='fas fa-check-circle mr-1'></i> AI Models Ready";
            btn.disabled = false; btn.classList.remove("opacity-50");
        } else {
            msg.className = "mt-3 alert alert-error text-xs text-center";
            msg.innerHTML = "<i class='fas fa-times mr-1'></i> Model load failed — please refresh.";
        }
    });
}

async function regStartCam() {
    const err = document.getElementById("reg-cam-err");
    err.classList.add("hidden");
    try {
        _regStream = await navigator.mediaDevices.getUserMedia(
            { video: { facingMode: "user", width: 640, height: 480 }, audio: false }
        );
        document.getElementById("reg-vid").srcObject = _regStream;
        document.getElementById("reg-start-btn").classList.add("hidden");
        document.getElementById("reg-capture-btn").classList.remove("hidden");
        const ring = document.getElementById("reg-ring");
        ring.style.borderColor = "#22c55e"; ring.style.borderStyle = "solid";
        ring.classList.add("ring-pulse");
        document.getElementById("reg-ring-txt").textContent = "Look straight at the camera";
    } catch (e) {
        err.classList.remove("hidden");
        err.textContent = e.name === "NotAllowedError"
            ? "Camera permission denied. Please allow camera access in browser settings."
            : "Camera unavailable: " + e.message;
    }
}

async function regCapture() {
    const video  = document.getElementById("reg-vid");
    const canvas = document.getElementById("reg-canvas");
    const ringTxt = document.getElementById("reg-ring-txt");
    const ring    = document.getElementById("reg-ring");
    const err     = document.getElementById("reg-cam-err");

    err.classList.add("hidden");
    ring.style.borderColor = "#f59e0b";
    ringTxt.textContent = "Scanning…";
    document.getElementById("reg-capture-btn").disabled = true;

    canvas.width  = video.videoWidth  || 640;
    canvas.height = video.videoHeight || 480;
    const ctx = canvas.getContext("2d");
    // Mirror-flip for selfie appearance
    ctx.translate(canvas.width, 0); ctx.scale(-1, 1);
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    ctx.setTransform(1, 0, 0, 1, 0, 0);

    const imageB64 = canvas.toDataURL("image/jpeg", 0.85);

    try {
        const det = await faceapi
            .detectSingleFace(canvas, new faceapi.TinyFaceDetectorOptions({ inputSize: 416, scoreThreshold: 0.5 }))
            .withFaceLandmarks()
            .withFaceDescriptor();

        if (!det) {
            ring.style.borderColor = "#ef4444";
            ringTxt.textContent = "No face detected!";
            err.classList.remove("hidden");
            err.textContent = "No face detected. Ensure good lighting and face the camera directly.";
            document.getElementById("reg-capture-btn").disabled = false;
            return;
        }

        ring.style.borderColor = "#22c55e";
        ringTxt.textContent = "✅ Face captured!";
        stopFaceScanner();
        if (window._faceScanCB) window._faceScanCB(Array.from(det.descriptor), imageB64);
    } catch (e) {
        ringTxt.textContent = "Error: " + e.message;
        document.getElementById("reg-capture-btn").disabled = false;
    }
}

function stopFaceScanner() {
    if (_regStream) { _regStream.getTracks().forEach(t => t.stop()); _regStream = null; }
}
