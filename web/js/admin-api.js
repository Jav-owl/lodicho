import { ErrorAPI } from "./api.js";

const PRODUCTION_ORIGIN = "https://lodicho.zamarrito.com";
const ES_LOCAL = ["localhost", "127.0.0.1"].includes(location.hostname);
const BASE_URL = `${ES_LOCAL ? PRODUCTION_ORIGIN : ""}/api/v1/admin`;

const TOKEN_KEY = "lodicho:admin_token";

export function obtenerToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function guardarToken(token) {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function cerrarSesion() {
  sessionStorage.removeItem(TOKEN_KEY);
}

async function llamar(path, opciones = {}) {
  const token = obtenerToken();
  const headers = { ...(opciones.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(`${BASE_URL}${path}`, { ...opciones, headers });
  } catch (causa) {
    throw new ErrorAPI("No se pudo conectar con el servidor.", causa);
  }

  if (res.status === 401) {
    cerrarSesion();
    throw new ErrorAPI("Sesión inválida o expirada — vuelve a ingresar.");
  }

  let cuerpo = null;
  try {
    cuerpo = await res.json();
  } catch {}

  if (!res.ok) {
    const error = new ErrorAPI(
      typeof cuerpo?.detail === "string" ? cuerpo.detail : `El servidor respondió ${res.status}.`
    );
    error.detalle = cuerpo?.detail;
    throw error;
  }

  return cuerpo;
}

export async function login(password) {
  let res;
  try {
    res = await fetch(`${BASE_URL}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
  } catch (causa) {
    throw new ErrorAPI("No se pudo conectar con el servidor.", causa);
  }

  let cuerpo = null;
  try {
    cuerpo = await res.json();
  } catch {}

  if (!res.ok) {
    throw new ErrorAPI(typeof cuerpo?.detail === "string" ? cuerpo.detail : "Contraseña incorrecta.");
  }

  guardarToken(cuerpo.token);
  return cuerpo;
}

// Docling puede tardar varios minutos y Cloudflare corta cualquier peticion a
// los ~100s (524): el POST solo inicia el trabajo y aca se consulta su estado.
const CONVERSION_POLL_MS = 3000;
const CONVERSION_MAX_MS = 30 * 60 * 1000;

export async function convertir(tipo, archivoPdf) {
  const formData = new FormData();
  formData.set("tipo", tipo);
  formData.set("pdf", archivoPdf);
  const { job_id } = await llamar("/documentos/convertir", { method: "POST", body: formData });

  const inicio = Date.now();
  while (Date.now() - inicio < CONVERSION_MAX_MS) {
    await new Promise((resolver) => setTimeout(resolver, CONVERSION_POLL_MS));
    const estado = await llamar(`/documentos/convertir/${job_id}`);
    if (estado.estado === "listo") return estado.resultado;
    if (estado.estado === "error") throw new ErrorAPI(estado.detalle || "No se pudo convertir el PDF.");
  }
  throw new ErrorAPI("La conversión tarda demasiado. Revisa el servidor e inténtalo de nuevo.");
}

export function importarMarkdown(tipo, archivoMd) {
  const formData = new FormData();
  formData.set("tipo", tipo);
  formData.set("markdown_file", archivoMd);
  return llamar("/documentos/importar-markdown", { method: "POST", body: formData });
}

export function obtenerBorrador(id) {
  return llamar(`/documentos/borradores/${id}`);
}

export function actualizarBorrador(id, markdown, meta) {
  return llamar(`/documentos/borradores/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ markdown, meta }),
  });
}

export function validarBorrador(id) {
  return llamar(`/documentos/borradores/${id}/validar`, { method: "POST" });
}

export function confirmarBorrador(id) {
  return llamar(`/documentos/borradores/${id}/confirmar`, { method: "POST" });
}

export function descartarBorrador(id) {
  return llamar(`/documentos/borradores/${id}`, { method: "DELETE" });
}

export function ingestarDocumento(docId) {
  return llamar(`/documentos/${encodeURIComponent(docId)}/ingestar`, { method: "POST" });
}

export function listarDocumentos() {
  return llamar("/documentos");
}

export function editarDocumento(docId) {
  return llamar(`/documentos/${encodeURIComponent(docId)}/editar`, { method: "POST" });
}

export function listarCandidaturas() {
  return llamar("/candidaturas");
}

export function crearCandidatura(datos) {
  return llamar("/candidaturas", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(datos),
  });
}

export function crearCandidato(candidaturaId, datos) {
  return llamar(`/candidaturas/${candidaturaId}/candidatos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(datos),
  });
}