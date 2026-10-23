const PRODUCTION_ORIGIN = "https://lodicho.zamarrito.com";
const ES_LOCAL = ["localhost", "127.0.0.1"].includes(location.hostname);
const BASE_URL = `${ES_LOCAL ? PRODUCTION_ORIGIN : ""}/api/v1`;

export class ErrorAPI extends Error {
  constructor(mensaje, causa) {
    super(mensaje);
    this.name = "ErrorAPI";
    this.causa = causa;
  }
}

export async function listarCandidaturas() {
  const res = await fetch(`${BASE_URL}/candidaturas`);
  if (!res.ok) throw new ErrorAPI(`El servidor respondió ${res.status}.`);
  return await res.json();
}

export async function obtenerCandidatura(id) {
  const res = await fetch(`${BASE_URL}/candidaturas/${encodeURIComponent(id)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new ErrorAPI(`El servidor respondió ${res.status}.`);
  return await res.json();
}

export async function obtenerEstado() {
  try {
    const res = await fetch(`${BASE_URL}/estado`);
    if (!res.ok) return { modo_silencio_electoral: false };
    return await res.json();
  } catch {
    return { modo_silencio_electoral: false };
  }
}

export async function enviarConsulta(payload, handlers) {
  const formData = new FormData();
  formData.set("tipo_input", payload.tipoInput);
  if (payload.texto) formData.set("texto", payload.texto);
  if (payload.urlFuente) formData.set("url_fuente", payload.urlFuente);
  if (payload.audioBlob) formData.set("audio", payload.audioBlob, "consulta.webm");
  if (payload.candidaturaId) formData.set("candidatura_id", payload.candidaturaId);

  let response;
  try {
    response = await fetch(`${BASE_URL}/consulta`, { method: "POST", body: formData });
  } catch (causa) {
    handlers.onError(new ErrorAPI("No se pudo conectar con el servidor.", causa));
    return;
  }

  await leerStreamSSE(response, handlers);
}

export async function pedirVeredicto(declaracionId, candidaturaId, handlers) {
  let response;
  try {
    response = await fetch(`${BASE_URL}/veredicto`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ declaracion_id: declaracionId, candidatura_id: candidaturaId }),
    });
  } catch (causa) {
    handlers.onError(new ErrorAPI("No se pudo conectar con el servidor.", causa));
    return;
  }

  await leerStreamSSE(response, handlers);
}

async function leerStreamSSE(response, handlers) {
  if (!response.ok || !response.body) {
    handlers.onError(new ErrorAPI(`El servidor respondió ${response.status}.`));
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let separador;
      while ((separador = buffer.indexOf("\n\n")) !== -1) {
        const bloque = buffer.slice(0, separador);
        buffer = buffer.slice(separador + 2);
        procesarBloqueSSE(bloque, handlers);
      }
    }
  } catch (causa) {
    handlers.onError(new ErrorAPI("Se perdió la conexión durante la consulta.", causa));
    return;
  }

  handlers.onFin?.();
}

function procesarBloqueSSE(bloque, handlers) {
  let evento = "message";
  const lineasData = [];
  for (const linea of bloque.split("\n")) {
    if (linea.startsWith("event:")) evento = linea.slice(6).trim();
    else if (linea.startsWith("data:")) lineasData.push(linea.slice(5).trim());
  }
  if (lineasData.length === 0) return;

  try {
    const data = JSON.parse(lineasData.join("\n"));
    handlers.onEvento(evento, data);
  } catch (causa) {
    handlers.onError(new ErrorAPI("Respuesta del servidor no válida.", causa));
  }
}