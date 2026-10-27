import { ICONS, mostrarToast } from "../icons.js";
import { escapeHtml } from "../util.js";

const CAMPOS_POR_TIPO = {
  marco_legal: [
    { clave: "nivel_gobierno", etiqueta: "Nivel de gobierno", tipo: "text", placeholder: "cantonal | provincial | parroquial_rural" },
    { clave: "jurisdiccion_dpa", etiqueta: "Jurisdicción DPA (opcional)", tipo: "text", placeholder: "0207" },
  ],
  plan_trabajo: [
    { clave: "dignidad", etiqueta: "Dignidad", tipo: "text", placeholder: "vocal_junta_parroquial" },
    { clave: "organizacion", etiqueta: "Organización política", tipo: "text", placeholder: "Partido Y" },
    { clave: "lista_numero", etiqueta: "Número de lista", tipo: "text", placeholder: "18" },
    { clave: "periodo", etiqueta: "Período", tipo: "text", placeholder: "2027-2031" },
    { clave: "jurisdiccion_dpa", etiqueta: "Jurisdicción DPA", tipo: "text", placeholder: "0207" },
  ],
  contexto: [{ clave: "jurisdiccion_dpa", etiqueta: "Jurisdicción DPA", tipo: "text", placeholder: "0207" }],
};

const SECCIONES = [
  { clave: "subir", etiqueta: "Ingresar Documento" },
  { clave: "documentos", etiqueta: "Corpus" },
  { clave: "candidaturas", etiqueta: "Candidaturas" },
];

export async function render(container) {
  const api = await import("../admin-api.js");
  if (!api.obtenerToken()) {
    renderLogin(container, api);
    return;
  }
  renderPanel(container, api, "subir");
}

function renderLogin(container, api) {
  container.innerHTML = `
    <div class="admin-login-wrap">
      <div class="admin-login-card">
        <div class="admin-login-card__icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="11" width="18" height="11" rx="2"></rect>
            <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
          </svg>
        </div>
        <h1 class="admin-login-card__title">Acceso Editorial</h1>
        <p class="admin-login-card__subtitle">Autenticación para ingesta y conversión de documentos oficiales.</p>
        <div class="admin-login-card__field">
          <label for="admin-password">Contraseña de administrador</label>
          <input type="password" class="console-input" id="admin-password" placeholder="••••••••" autocomplete="current-password" />
        </div>
        <button type="button" class="btn-primary" id="admin-login-btn" style="width:100%;">Ingresar</button>
        <p class="field-hint" id="admin-login-error" style="color: var(--veredicto-falso-text); margin-top:12px; font-size:13px;"></p>
      </div>
    </div>
  `;

  const input = container.querySelector("#admin-password");
  const boton = container.querySelector("#admin-login-btn");
  const error = container.querySelector("#admin-login-error");

  async function intentar() {
    if (!input.value) return;
    boton.disabled = true;
    error.textContent = "";
    try {
      await api.login(input.value);
      renderPanel(container, api, "subir");
    } catch (err) {
      error.textContent = err.message;
    } finally {
      boton.disabled = false;
    }
  }

  boton.addEventListener("click", intentar);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") intentar(); });
  input.focus();
}

function renderPanel(container, api, seccion) {
  container.innerHTML = `
    <div class="dash-hero">
      <h1 class="dash-hero__title">Gestión Editorial</h1>
      <p class="dash-hero__subtitle">Conversión estructurada con Docling, validación de reglas e indexación en Qdrant.</p>
    </div>
    
    <div class="console-tabs" role="tablist">
      ${SECCIONES.map((s) => `
        <button type="button" class="console-tab" aria-selected="${s.clave === seccion ? "true" : "false"}" data-seccion="${s.clave}">
          ${escapeHtml(s.etiqueta)}
        </button>
      `).join("")}
      <button type="button" class="console-tab" id="admin-salir" style="color: var(--veredicto-falso-text); margin-left: auto;">Cerrar Sesión</button>
    </div>
    
    <div id="admin-contenido"></div>
  `;

  container.querySelectorAll(".console-tab[data-seccion]").forEach((boton) => {
    boton.addEventListener("click", () => renderPanel(container, api, boton.dataset.seccion));
  });

  container.querySelector("#admin-salir").addEventListener("click", () => {
    api.cerrarSesion();
    renderLogin(container, api);
  });

  const contenido = container.querySelector("#admin-contenido");
  if (seccion === "documentos") renderDocumentos(contenido, api);
  else if (seccion === "candidaturas") renderCandidaturas(contenido, api);
  else renderSubir(contenido, api);
}

async function renderDocumentos(container, api) {
  container.innerHTML = `<div class="loading-state"><div class="apple-spinner"></div><p>Cargando documentos del corpus...</p></div>`;
  let documentos;
  try {
    ({ documentos } = await api.listarDocumentos());
  } catch (err) {
    container.innerHTML = `<div class="banner banner--danger">${escapeHtml(err.message)}</div>`;
    return;
  }

  if (!documentos.length) {
    container.innerHTML = `
      <div class="console-card" style="text-align:center; padding:36px;">
        <p style="color:var(--color-text-muted);">No hay documentos registrados en el corpus todavía.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = `
    <div class="console-card" style="padding: 0; overflow-x: auto;">
      <table style="width:100%; border-collapse:collapse; text-align:left;">
        <thead>
          <tr style="border-bottom: 0.5px solid var(--color-border); font-size:12px; color:var(--color-text-subtle);">
            <th style="padding:14px 18px;">doc_id</th>
            <th style="padding:14px 18px;">Tipo</th>
            <th style="padding:14px 18px;">Chunks</th>
            <th style="padding:14px 18px;">Acción</th>
          </tr>
        </thead>
        <tbody>
          ${documentos.map(d => `
            <tr style="border-bottom: 0.5px solid var(--color-border-subtle); font-size:13.5px;">
              <td style="padding:14px 18px;"><code style="font-family:var(--font-mono);">${escapeHtml(d.doc_id)}</code></td>
              <td style="padding:14px 18px;">${escapeHtml(d.tipo)}</td>
              <td style="padding:14px 18px;">${d.n_chunks}</td>
              <td style="padding:14px 18px; display:flex; gap:8px;">
                <button type="button" class="btn-secondary" style="padding:5px 12px; font-size:12px;" data-editar="${escapeHtml(d.doc_id)}">
                  Editar
                </button>
                <button type="button" class="btn-secondary" style="padding:5px 12px; font-size:12px;" data-reingestar="${escapeHtml(d.doc_id)}">
                  Reindexar
                </button>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;

  container.querySelectorAll("[data-reingestar]").forEach((boton) => {
    boton.addEventListener("click", async () => {
      const docId = boton.dataset.reingestar;
      boton.disabled = true;
      try {
        const r = await api.ingestarDocumento(docId);
        mostrarToast(`${docId}: ${r.n_chunks} chunks indexados`);
      } catch (err) {
        mostrarToast(err.message);
      } finally {
        boton.disabled = false;
      }
    });
  });

  container.querySelectorAll("[data-editar]").forEach((boton) => {
    boton.addEventListener("click", async () => {
      const docId = boton.dataset.editar;
      boton.disabled = true;
      try {
        const r = await api.editarDocumento(docId);
        await renderRevisar(container, api, { borrador_id: r.borrador_id, markdown: r.markdown, tipo: r.tipo }, r.meta);
      } catch (err) {
        mostrarToast(err.message);
        boton.disabled = false;
      }
    });
  });
}

async function renderCandidaturas(container, api) {
  container.innerHTML = `<div class="loading-state"><div class="apple-spinner"></div><p>Cargando candidaturas...</p></div>`;
  let candidaturas;
  try {
    candidaturas = await api.listarCandidaturas();
  } catch (err) {
    container.innerHTML = `<div class="banner banner--danger">${escapeHtml(err.message)}</div>`;
    return;
  }

  container.innerHTML = `
    <div class="console-card">
      <h2 style="font-size:17px; font-weight:700; margin-bottom:18px;">Registrar Nueva Candidatura</h2>
      <div id="admin-candidatura-error"></div>
      
      <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:16px;">
        <div>
          <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">Organización política</label>
          <input type="text" class="console-input" id="cand-organizacion" placeholder="Ej: Movimiento Político" />
        </div>
        <div>
          <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">Número de lista</label>
          <input type="text" class="console-input" id="cand-lista" placeholder="Ej: 18" />
        </div>
        <div>
          <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">Dignidad</label>
          <input type="text" class="console-input" id="cand-dignidad" placeholder="Ej: prefecto" />
        </div>
        <div>
          <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">Jurisdicción DPA</label>
          <input type="text" class="console-input" id="cand-jurisdiccion" placeholder="0200" />
        </div>
        <div>
          <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">Período</label>
          <input type="text" class="console-input" id="cand-periodo" placeholder="2027-2031" />
        </div>
        <div>
          <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">Estado del plan</label>
          <select class="console-select" id="cand-estado-plan">
            <option value="registrado">Registrado ante el CNE</option>
            <option value="sin_plan_registrado">No registró plan</option>
          </select>
        </div>
      </div>
      <button type="button" class="btn-primary" id="cand-guardar-btn" style="margin-top:20px;">Guardar Candidatura</button>
    </div>

    <div style="margin-top:24px; display:flex; flex-direction:column; gap:16px;">
      ${candidaturas.length ? candidaturas.map(candidaturaCardHtml).join("") : `
        <p style="color:var(--color-text-subtle); text-align:center; padding:24px;">Todavía no hay candidaturas registradas.</p>
      `}
    </div>
  `;

  const errorEl = container.querySelector("#admin-candidatura-error");
  container.querySelector("#cand-guardar-btn").addEventListener("click", async (evento) => {
    const datos = {
      organizacion_politica: container.querySelector("#cand-organizacion").value.trim(),
      lista_numero: container.querySelector("#cand-lista").value.trim(),
      dignidad: container.querySelector("#cand-dignidad").value.trim(),
      jurisdiccion_dpa: container.querySelector("#cand-jurisdiccion").value.trim(),
      periodo: container.querySelector("#cand-periodo").value.trim(),
      estado_plan: container.querySelector("#cand-estado-plan").value,
    };

    if (Object.values(datos).some((v) => !v)) {
      errorEl.innerHTML = `<div class="banner banner--warning">Completa todos los campos obligatorios.</div>`;
      return;
    }

    evento.target.disabled = true;
    try {
      await api.crearCandidatura(datos);
      mostrarToast("Candidatura creada correctamente");
      renderCandidaturas(container, api);
    } catch (err) {
      errorEl.innerHTML = `<div class="banner banner--danger">${escapeHtml(err.message)}</div>`;
      evento.target.disabled = false;
    }
  });

  container.querySelectorAll("[data-accion='agregar-candidato']").forEach((boton) => {
    boton.addEventListener("click", async () => {
      const candidaturaId = boton.dataset.candidaturaId;
      const tarjeta = boton.closest("[data-candidatura-card]");
      const nombreInput = tarjeta.querySelector("[data-campo='cand-nombre']");
      const posicionInput = tarjeta.querySelector("[data-campo='cand-posicion']");
      const errorLocalEl = tarjeta.querySelector("[data-candidato-error]");

      const nombre = nombreInput.value.trim();
      const posicion = Number(posicionInput.value);

      if (!nombre || !posicion || posicion < 1) {
        errorLocalEl.innerHTML = `<div class="banner banner--warning" style="margin-top:8px;">Nombre y posición en lista son obligatorios.</div>`;
        return;
      }

      boton.disabled = true;
      try {
        await api.crearCandidato(candidaturaId, { nombre, posicion_lista: posicion });
        mostrarToast("Candidato agregado correctamente");
        renderCandidaturas(container, api);
      } catch (err) {
        errorLocalEl.innerHTML = `<div class="banner banner--danger" style="margin-top:8px;">${escapeHtml(err.message)}</div>`;
        boton.disabled = false;
      }
    });
  });
}

function candidaturaCardHtml(candidatura) {
  const candidatos = candidatura.candidatos || [];
  const candidatosHtml = candidatos.length
    ? candidatos.map((c) => `
        <li>${escapeHtml(String(c.posicion_lista))}. ${escapeHtml(c.nombre)}</li>
      `).join("")
    : `<li style="color:var(--color-text-subtle);">Sin candidatos registrados.</li>`;

  return `
    <div class="console-card" data-candidatura-card data-candidatura-id="${escapeHtml(String(candidatura.id))}">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:10px;">
        <div>
          <strong>${escapeHtml(candidatura.organizacion_politica)}</strong> — Lista ${escapeHtml(candidatura.lista_numero)}
          <div style="font-size:13px; color:var(--color-text-subtle);">${escapeHtml(candidatura.dignidad)} · ${escapeHtml(candidatura.jurisdiccion_dpa)} · ${escapeHtml(candidatura.periodo)}</div>
        </div>
      </div>
      <ul style="margin:0 0 12px; padding-left:18px; font-size:14px;">${candidatosHtml}</ul>
      <div data-candidato-error></div>
      <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:flex-end;">
        <div style="flex:2; min-width:140px;">
          <label style="font-size:11px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:4px;">Nombre del candidato</label>
          <input type="text" class="console-input" data-campo="cand-nombre" placeholder="Nombre completo" />
        </div>
        <div style="flex:1; min-width:80px;">
          <label style="font-size:11px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:4px;">Posición</label>
          <input type="number" min="1" class="console-input" data-campo="cand-posicion" placeholder="1" />
        </div>
        <button type="button" class="btn-secondary" data-accion="agregar-candidato" data-candidatura-id="${escapeHtml(String(candidatura.id))}">Agregar</button>
      </div>
    </div>
  `;
}

function renderSubir(container, api) {
  container.innerHTML = `
    <div class="console-card">
      <div style="margin-bottom: 16px;">
        <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">Tipo de fuente documental</label>
        <select class="console-select" id="admin-tipo">
          <option value="marco_legal">Marco Legal (COOTAD, Leyes)</option>
          <option value="plan_trabajo">Plan de Trabajo Oficial CNE</option>
          <option value="contexto">Contexto Territorial</option>
        </select>
      </div>

      <div style="margin-bottom: 16px; display:flex; gap:8px;">
        <button type="button" class="btn-secondary" id="admin-modo-pdf" style="flex:1;">PDF (Docling)</button>
        <button type="button" class="btn-secondary" id="admin-modo-md" style="flex:1;">Markdown (.md)</button>
      </div>

      <div class="dropzone-apple" id="admin-dropzone" style="margin-bottom: 20px;">
        <div class="dropzone-apple__icon">${ICONS.upload}</div>
        <div class="dropzone-apple__title" id="admin-dropzone-title">Arrastra o haz clic para subir el PDF oficial</div>
        <div class="dropzone-apple__subtitle" id="admin-dropzone-subtitle">Se procesará server-side estructuradamente con Docling</div>
        <input type="file" id="admin-archivo" accept="application/pdf" hidden />
      </div>

      <button type="button" class="btn-primary" id="admin-convertir-btn" style="width:100%;">Convertir a Markdown</button>
      <div id="admin-subir-estado" style="margin-top:16px;"></div>
    </div>
  `;

  const selectTipo = container.querySelector("#admin-tipo");
  const inputArchivo = container.querySelector("#admin-archivo");
  const dropzone = container.querySelector("#admin-dropzone");
  const dropzoneTitle = container.querySelector("#admin-dropzone-title");
  const dropzoneSubtitle = container.querySelector("#admin-dropzone-subtitle");
  const boton = container.querySelector("#admin-convertir-btn");
  const estadoEl = container.querySelector("#admin-subir-estado");
  const btnPdf = container.querySelector("#admin-modo-pdf");
  const btnMd = container.querySelector("#admin-modo-md");

  let modo = "pdf";

  function actualizarModo(nuevoModo) {
    modo = nuevoModo;
    inputArchivo.value = "";
    const esPdf = modo === "pdf";
    inputArchivo.accept = esPdf ? "application/pdf" : ".md,text/markdown";
    dropzoneTitle.textContent = esPdf
      ? "Arrastra o haz clic para subir el PDF oficial"
      : "Arrastra o haz clic para subir el archivo .md";
    dropzoneSubtitle.textContent = esPdf
      ? "Se procesará server-side estructuradamente con Docling"
      : "Se usa tal cual, sin pasar por Docling";
    boton.textContent = esPdf ? "Convertir a Markdown" : "Continuar con este Markdown";
    btnPdf.classList.toggle("btn-primary", esPdf);
    btnPdf.classList.toggle("btn-secondary", !esPdf);
    btnMd.classList.toggle("btn-primary", !esPdf);
    btnMd.classList.toggle("btn-secondary", esPdf);
  }

  btnPdf.addEventListener("click", () => actualizarModo("pdf"));
  btnMd.addEventListener("click", () => actualizarModo("md"));

  dropzone.addEventListener("click", () => inputArchivo.click());
  inputArchivo.addEventListener("change", () => {
    if (inputArchivo.files[0]) dropzoneSubtitle.textContent = inputArchivo.files[0].name;
  });

  boton.addEventListener("click", async () => {
    const archivo = inputArchivo.files[0];
    if (!archivo) {
      estadoEl.innerHTML = `<div class="banner banner--warning">Selecciona un archivo primero.</div>`;
      return;
    }
    boton.disabled = true;
    estadoEl.innerHTML = modo === "pdf"
      ? `<div class="loading-state"><div class="apple-spinner"></div><p>Docling está extrayendo texto y tablas del documento... puede tardar varios minutos, no cierres esta pantalla.</p></div>`
      : `<div class="loading-state"><div class="apple-spinner"></div><p>Leyendo el markdown...</p></div>`;

    try {
      const resultado = modo === "pdf"
        ? await api.convertir(selectTipo.value, archivo)
        : await api.importarMarkdown(selectTipo.value, archivo);
      await renderRevisar(container, api, resultado);
    } catch (err) {
      estadoEl.innerHTML = `<div class="banner banner--danger">${escapeHtml(err.message)}</div>`;
      boton.disabled = false;
    }
  });

  actualizarModo("pdf");
}

async function renderRevisar(container, api, datosIniciales, metaInicial = {}) {
  const estado = {
    borradorId: datosIniciales.borrador_id,
    tipo: datosIniciales.tipo,
    markdown: datosIniciales.markdown,
    meta: metaInicial,
  };
  const editandoExistente = Boolean(metaInicial.doc_id);

  let candidaturasCache = null;
  async function candidaturas() {
    if (!candidaturasCache) {
      try {
        candidaturasCache = await api.listarCandidaturas();
      } catch {
        candidaturasCache = [];
      }
    }
    return candidaturasCache;
  }

  container.innerHTML = `
    <div class="console-card">
      <h2 style="font-size:17px; font-weight:700; margin-bottom:18px;">Paso 2: Edición y Validación Editorial</h2>
      ${editandoExistente ? `<div class="banner banner--warning" style="margin-bottom:16px;">Estás editando un documento ya ingestado. Si cambias el tipo, el archivo y los fragmentos viejos se eliminan al confirmar — recuerda volver a pulsar "Ingestar" después.</div>` : ""}
      <div id="admin-validacion"></div>

      <div style="display:flex; flex-direction:column; gap:16px;">
        <div>
          <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">Tipo de documento</label>
          <select class="console-select" id="admin-tipo-doc">
            <option value="marco_legal">Marco Legal (COOTAD, Leyes)</option>
            <option value="plan_trabajo">Plan de Trabajo Oficial CNE</option>
            <option value="contexto">Contexto Territorial</option>
          </select>
        </div>

        <div>
          <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">doc_id (identificador único)</label>
          <input type="text" class="console-input" id="admin-doc-id" placeholder="ej: plan-bolivar-simiatug-junta-18-2027" />
        </div>

        <div id="admin-campos-tipo"></div>

        <div>
          <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">URL fuente oficial (CNE)</label>
          <input type="url" class="console-input" id="admin-fuente-url" placeholder="https://..." />
        </div>

        <div>
          <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">Revisor editorial responsable</label>
          <input type="text" class="console-input" id="admin-revisado-por" placeholder="nombre.apellido" />
        </div>

        <div>
          <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">Cuerpo del Documento (Markdown)</label>
          <textarea class="console-textarea" id="admin-markdown" style="min-height:280px; font-family:var(--font-mono); font-size:13px;"></textarea>
        </div>
      </div>

      <div style="display:flex; gap:12px; margin-top:24px; flex-wrap:wrap;">
        <button type="button" class="btn-secondary" id="admin-validar-btn" style="flex:1;">Validar Reglas</button>
        <button type="button" class="btn-primary" id="admin-confirmar-btn" style="flex:1;">Confirmar y Publicar</button>
      </div>
      <button type="button" class="btn-secondary" id="admin-cancelar-btn" style="margin-top:12px; width:100%;">Descartar Borrador</button>
    </div>
  `;

  container.querySelector("#admin-markdown").value = estado.markdown;
  container.querySelector("#admin-doc-id").value = estado.meta.doc_id || "";
  container.querySelector("#admin-fuente-url").value = estado.meta.fuente_url || "";
  container.querySelector("#admin-revisado-por").value = estado.meta.revisado_por || "";

  const selectTipoDoc = container.querySelector("#admin-tipo-doc");
  selectTipoDoc.value = estado.tipo;

  async function pintarCamposTipo() {
    const camposEspecificos = CAMPOS_POR_TIPO[estado.tipo] || [];
    const contenedor = container.querySelector("#admin-campos-tipo");

    let selectorCandidatura = "";
    if (estado.tipo === "plan_trabajo") {
      const lista = await candidaturas();
      selectorCandidatura = `
        <div style="margin-bottom:16px;">
          <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">Candidatura asociada</label>
          <select class="console-select" id="admin-campo-candidatura_id" data-campo="candidatura_id">
            <option value="">-- Seleccionar Candidatura --</option>
            ${lista.map(c => `<option value="${c.id}" ${String(c.id) === String(estado.meta.candidatura_id ?? "") ? "selected" : ""}>${escapeHtml(`${c.organizacion_politica} - Lista ${c.lista_numero} (${c.dignidad})`)}</option>`).join("")}
          </select>
        </div>
      `;
    }

    contenedor.innerHTML = `
      ${selectorCandidatura}
      <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:16px;">
        ${camposEspecificos.map(c => `
          <div>
            <label style="font-size:12px; font-weight:600; color:var(--color-text-subtle); display:block; margin-bottom:6px;">${escapeHtml(c.etiqueta)}</label>
            <input type="${c.tipo}" class="console-input" id="admin-campo-${c.clave}" data-campo="${c.clave}" placeholder="${escapeHtml(c.placeholder)}" />
          </div>
        `).join("")}
      </div>
    `;

    camposEspecificos.forEach((c) => {
      const input = contenedor.querySelector(`#admin-campo-${c.clave}`);
      if (input && estado.meta[c.clave] != null) input.value = estado.meta[c.clave];
    });
  }

  await pintarCamposTipo();

  selectTipoDoc.addEventListener("change", async () => {
    estado.tipo = selectTipoDoc.value;
    await pintarCamposTipo();
  });

  function leerFormulario() {
    const meta = { tipo: estado.tipo, vigente: true };
    const docId = container.querySelector("#admin-doc-id").value.trim();
    if (docId) meta.doc_id = docId;
    const fuenteUrl = container.querySelector("#admin-fuente-url").value.trim();
    if (fuenteUrl) meta.fuente_url = fuenteUrl;
    const revisadoPor = container.querySelector("#admin-revisado-por").value.trim();
    if (revisadoPor) meta.revisado_por = revisadoPor;

    container.querySelectorAll("[data-campo]").forEach((input) => {
      const valor = input.value.trim();
      if (!valor) return;
      meta[input.dataset.campo] = input.dataset.campo === "candidatura_id" ? Number(valor) : valor;
    });

    return { markdown: container.querySelector("#admin-markdown").value, meta };
  }

  function mostrarValidacion(resultado) {
    const el = container.querySelector("#admin-validacion");
    const partes = [];
    if (resultado.errores?.length) {
      partes.push(`<div class="banner banner--danger" style="flex-direction:column; align-items:flex-start;"><strong>Errores que impiden guardar:</strong><ul style="margin-top:6px; padding-left:18px;">${resultado.errores.map(e => `<li>${escapeHtml(e)}</li>`).join("")}</ul></div>`);
    }
    if (resultado.warnings?.length) {
      partes.push(`<div class="banner banner--warning" style="flex-direction:column; align-items:flex-start;"><strong>Observaciones:</strong><ul style="margin-top:6px; padding-left:18px;">${resultado.warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("")}</ul></div>`);
    }
    el.innerHTML = partes.join("");
  }

  container.querySelector("#admin-validar-btn").addEventListener("click", async () => {
    const { markdown, meta } = leerFormulario();
    const btn = container.querySelector("#admin-validar-btn");
    btn.disabled = true;
    try {
      await api.actualizarBorrador(estado.borradorId, markdown, meta);
      const resultado = await api.validarBorrador(estado.borradorId);
      mostrarValidacion(resultado);
      if (!resultado.errores?.length) mostrarToast("Validación superada");
    } catch (err) {
      mostrarValidacion({ errores: [err.message], warnings: [] });
    } finally {
      btn.disabled = false;
    }
  });

  container.querySelector("#admin-confirmar-btn").addEventListener("click", async () => {
    const { markdown, meta } = leerFormulario();
    const btn = container.querySelector("#admin-confirmar-btn");
    btn.disabled = true;
    try {
      await api.actualizarBorrador(estado.borradorId, markdown, meta);
      const resultado = await api.confirmarBorrador(estado.borradorId);
      renderListo(container, api, resultado);
    } catch (err) {
      if (err.detalle?.errores) mostrarValidacion(err.detalle);
      else mostrarValidacion({ errores: [err.message], warnings: [] });
      btn.disabled = false;
    }
  });

  container.querySelector("#admin-cancelar-btn").addEventListener("click", async () => {
    try { await api.descartarBorrador(estado.borradorId); } catch {}
    renderSubir(container, api);
  });
}

function renderListo(container, api, resultado) {
  container.innerHTML = `
    <div class="console-card">
      <div class="veredicto-badge veredicto-badge--viable_y_en_plan" style="margin-bottom:14px;">Documento commiteado al corpus</div>
      <h2 style="font-size:18px; font-weight:700; margin-bottom:12px;">Paso 3: Ingesta Vectorial</h2>
      
      <div style="background:var(--color-surface-muted); padding:16px; border-radius:var(--radius-sm); font-family:var(--font-mono); font-size:12px; margin-bottom:20px; word-break:break-all; line-height:1.6;">
        <strong>doc_id:</strong> ${escapeHtml(resultado.doc_id)}<br />
        <strong>git_sha:</strong> ${escapeHtml(resultado.git_sha)}<br />
        <strong>ruta:</strong> ${escapeHtml(resultado.ruta_md)}
      </div>
      
      <button type="button" class="btn-primary" id="admin-ingestar-btn" style="width:100%;">Indexar Chunks en Qdrant</button>
      <div id="admin-ingesta-estado" style="margin-top:14px;"></div>
      <button type="button" class="btn-secondary" id="admin-otro-btn" style="margin-top:14px; width:100%;">Cargar Otro Documento</button>
    </div>
  `;

  const estadoEl = container.querySelector("#admin-ingesta-estado");
  container.querySelector("#admin-ingestar-btn").addEventListener("click", async (evento) => {
    evento.target.disabled = true;
    estadoEl.innerHTML = `<div class="loading-state"><div class="apple-spinner"></div><p>Generando embeddings con Gemini e indexando en Qdrant...</p></div>`;
    try {
      const r = await api.ingestarDocumento(resultado.doc_id);
      estadoEl.innerHTML = `<div class="banner banner--viable" style="background:var(--veredicto-viable-bg); color:var(--veredicto-viable-text);">${ICONS.check} <span>Éxito: ${r.n_chunks} fragmentos indexados en Qdrant.</span></div>`;
    } catch (err) {
      estadoEl.innerHTML = `<div class="banner banner--danger">${escapeHtml(err.message)}</div>`;
      evento.target.disabled = false;
    }
  });

  container.querySelector("#admin-otro-btn").addEventListener("click", () => renderSubir(container, api));
}