import { ICONS } from "../icons.js";
import { transcribirAudio } from "../api.js";

const URL_RE = /^https?:\/\/\S+$/i;
const LIMITE_AUDIO_BYTES = 10 * 1024 * 1024;
// Deben coincidir con MAX_CHARS_CONSULTA y AUDIO_MAX_SEGUNDOS del backend (el servidor es quien manda).
const MAX_CHARS = 1000;
const MAX_AUDIO_S = 60;

const SUGGESTIONS = [
  "Castillo propone eliminar la tasa de mantenimiento vial",
  "Guerra propone invertir $50 millones en Misión Violeta",
  "Zambrano propone destinar el 20% del presupuesto a parroquias rurales"
];

export function crearComposer({ onEnviar }) {
  const el = document.createElement("div");
  el.className = "composer-card";

  el.innerHTML = `
    <textarea
      class="composer-textarea"
      id="composer-texto"
      placeholder="Escribe una afirmación (máx. 1000 caracteres) o presiona dictar (máx. 60 s)..."
      rows="3"
      maxlength="${MAX_CHARS}"
    ></textarea>

    <div class="audio-preview-chip" id="composer-chip" style="display:none;"></div>
    <input type="file" id="composer-file" accept="audio/*" style="display:none;" />

    <div class="composer-toolbar">
      <div class="composer-actions-left">
        <button type="button" class="composer-pill-btn" id="composer-mic" aria-label="Dictar declaración">
          ${ICONS.mic} <span id="composer-mic-label">Dictar</span>
        </button>
        <button type="button" class="composer-pill-btn" id="composer-attach" aria-label="Subir archivo de audio">
          ${ICONS.attach} <span>Audio</span>
        </button>
      </div>

      <button type="button" class="btn-primary" id="composer-enviar" disabled>
        ${ICONS.search} <span>Contrastar Declaración</span>
      </button>
    </div>

    <div class="composer-suggestions">
      ${SUGGESTIONS.map((s) => `<button type="button" class="suggestion-pill" data-texto="${s}">${s}</button>`).join("")}
    </div>

    <p class="field-hint" id="composer-status" style="margin-top: 8px; font-size: 12px; color: var(--veredicto-falso-text);"></p>
  `;

  const textarea = el.querySelector("#composer-texto");
  const chip = el.querySelector("#composer-chip");
  const botonMic = el.querySelector("#composer-mic");
  const botonAttach = el.querySelector("#composer-attach");
  const inputFile = el.querySelector("#composer-file");
  const botonEnviar = el.querySelector("#composer-enviar");
  const status = el.querySelector("#composer-status");

  let audioId = null;
  let transcribiendo = false;
  let temporizador = null;
  let grabando = false;
  let mediaRecorder = null;
  let chunks = [];
  let enviando = false;

  const ETIQUETA_ENVIAR = `${ICONS.search} <span>Contrastar Declaración</span>`;
  const ETIQUETA_ENVIANDO = `<div class="btn-spinner"></div> <span>Contrastando...</span>`;

  function actualizarEnviar() {
    botonEnviar.disabled = enviando || grabando || transcribiendo || !textarea.value.trim();
  }

  function mostrarChip(etiqueta) {
    chip.style.display = "inline-flex";
    chip.innerHTML = `
      <div class="audio-preview-chip__icon">${ICONS.mic}</div>
      <div class="audio-preview-chip__meta">
        <span class="audio-preview-chip__title">${etiqueta}</span>
        <span class="audio-preview-chip__subtitle">Audio listo para contrastar</span>
      </div>
      <button type="button" class="audio-preview-chip__close" aria-label="Eliminar audio">${ICONS.close}</button>
    `;
    chip.querySelector("button").addEventListener("click", quitarAudio);
  }

  function quitarAudio() {
    audioId = null;
    chip.style.display = "none";
    chip.innerHTML = "";
    actualizarEnviar();
  }

  textarea.addEventListener("input", actualizarEnviar);

  el.querySelectorAll(".suggestion-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      textarea.value = pill.dataset.texto;
      textarea.focus();
      actualizarEnviar();
    });
  });

  botonAttach.addEventListener("click", () => inputFile.click());

  inputFile.addEventListener("change", async () => {
    const archivo = inputFile.files[0];
    inputFile.value = "";
    if (!archivo) return;
    if (archivo.size > LIMITE_AUDIO_BYTES) {
      avisar("El archivo supera el límite permitido de 10 MB.", true);
      return;
    }
    const duracion = await duracionDe(archivo);
    if (duracion && duracion > MAX_AUDIO_S) {
      avisar(`El audio dura ${Math.round(duracion)} s y el máximo es ${MAX_AUDIO_S} s. Recórtalo e intenta de nuevo.`, true);
      return;
    }
    await transcribir(archivo, archivo.name);
  });

  function avisar(mensaje, error = false) {
    status.textContent = mensaje;
    status.style.color = error ? "var(--veredicto-falso-text)" : "var(--color-text-subtle)";
  }

  // Duracion segun el navegador; null si no la puede leer (el servidor la mide igual).
  function duracionDe(archivo) {
    return new Promise((resolve) => {
      const audio = new Audio();
      const url = URL.createObjectURL(archivo);
      const fin = (v) => { URL.revokeObjectURL(url); resolve(v); };
      audio.onloadedmetadata = () => fin(Number.isFinite(audio.duration) ? audio.duration : null);
      audio.onerror = () => fin(null);
      audio.src = url;
    });
  }

  // Sube el audio, deja el texto transcrito en el cuadro para que el usuario lo revise.
  async function transcribir(blob, etiqueta) {
    transcribiendo = true;
    actualizarEnviar();
    avisar("Transcribiendo audio...");
    try {
      const r = await transcribirAudio(blob);
      audioId = r.audio_id;
      textarea.value = r.texto;
      mostrarChip(etiqueta);
      avisar("Revisa que la transcripción sea lo que dijiste; puedes editarla antes de contrastar.");
    } catch (error) {
      avisar(error.message, true);
    } finally {
      transcribiendo = false;
      actualizarEnviar();
    }
  }

  botonMic.addEventListener("click", () => (grabando ? detenerDictado() : iniciarDictado()));

  async function iniciarDictado() {
    if (!window.isSecureContext) {
      avisar("El dictado requiere entorno seguro (HTTPS o localhost).", true);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      avisar("Este navegador no soporta captura de audio.", true);
      return;
    }

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      avisar("Permiso de micrófono denegado.", true);
      return;
    }

    chunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
    mediaRecorder.addEventListener("stop", async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" });
      if (blob.size === 0) { avisar("No se grabó nada. Intenta de nuevo.", true); return; }
      await transcribir(blob, "Dictado por voz");
    }, { once: true });
    mediaRecorder.start();

    grabando = true;
    botonMic.classList.add("composer-pill-btn--recording");
    botonMic.innerHTML = `${ICONS.stop} <span id="composer-mic-label">Detener</span>`;
    status.style.color = "var(--color-brand)";
    const inicio = Date.now();
    const actualizarTiempo = () => {
      const seg = Math.floor((Date.now() - inicio) / 1000);
      status.textContent = `Escuchando... ${seg} s de ${MAX_AUDIO_S} s`;
      if (seg >= MAX_AUDIO_S) detenerDictado();
    };
    actualizarTiempo();
    temporizador = setInterval(actualizarTiempo, 500);
    actualizarEnviar();
  }

  function detenerDictado() {
    if (!grabando) return;
    grabando = false;
    clearInterval(temporizador);
    botonMic.classList.remove("composer-pill-btn--recording");
    botonMic.innerHTML = `${ICONS.mic} <span>Dictar</span>`;
    if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
    actualizarEnviar();
  }

  botonEnviar.addEventListener("click", async () => {
    const texto = textarea.value.trim();
    let payload;
    if (audioId) {
      payload = { tipoInput: "voz", texto, audioId };
    } else if (URL_RE.test(texto)) {
      payload = { tipoInput: "url", urlFuente: texto };
    } else {
      payload = { tipoInput: "texto", texto };
    }

    enviando = true;
    botonEnviar.innerHTML = ETIQUETA_ENVIANDO;
    actualizarEnviar();

    textarea.value = "";
    quitarAudio();
    status.textContent = "";

    try {
      await onEnviar(payload);
    } finally {
      enviando = false;
      botonEnviar.innerHTML = ETIQUETA_ENVIAR;
      actualizarEnviar();
    }
  });

  actualizarEnviar();
  return el;
}