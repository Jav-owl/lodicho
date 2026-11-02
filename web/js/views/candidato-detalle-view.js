import { obtenerCandidatura } from "../api.js";
import { escapeHtml, formatearFecha } from "../util.js";
import { guardarCandidaturaPreseleccionada } from "../state.js";
import { veredictoBadgeHtml } from "../components/veredicto-badge.js";

const DIGNIDAD_LABEL = {
  prefecto: "Prefecto/a",
  viceprefecto: "Viceprefecto/a",
  alcalde: "Alcalde/sa",
  concejal: "Concejal",
  concejal_urbano: "Concejal urbano",
  concejal_rural: "Concejal rural",
  vocal_junta_parroquial: "Vocal de Junta Parroquial",
  presidente_junta_parroquial: "Presidente/a de Junta Parroquial",
};

function etiquetaDignidad(dignidad) {
  return DIGNIDAD_LABEL[dignidad] || dignidad;
}

export async function render(container, params) {
  container.innerHTML = `
    <div class="loading-state">
      <div class="apple-spinner"></div>
      <span>Cargando candidatura...</span>
    </div>
  `;

  let candidatura;
  try {
    candidatura = await obtenerCandidatura(params.id);
  } catch {
    container.innerHTML = errorHtml("No se pudo cargar esta candidatura. Intenta de nuevo.");
    return;
  }

  if (!candidatura) {
    container.innerHTML = errorHtml("No se encontró esta candidatura.");
    return;
  }

  const candidatos = candidatura.candidatos || [];
  const nombres = candidatos.length
    ? candidatos.map((c) => escapeHtml(c.nombre)).join(", ")
    : "Candidatura sin candidatos registrados";
  const sinPlan = candidatura.estado_plan !== "registrado";

  container.innerHTML = `
    <a href="#/candidatos" class="recent-card__cta" style="display:inline-block; margin-bottom:16px;">← Volver a candidatos</a>

    <div class="dash-hero">
      <h1 class="dash-hero__title">${nombres}</h1>
      <p class="dash-hero__subtitle">
        ${escapeHtml(etiquetaDignidad(candidatura.dignidad))} — ${escapeHtml(candidatura.organizacion_politica)}
        (Lista ${escapeHtml(candidatura.lista_numero)}) · Periodo ${escapeHtml(candidatura.periodo)}
      </p>
    </div>

    <div class="console-card">
      <div style="display:flex; flex-wrap:wrap; gap:10px; margin-bottom: ${sinPlan ? "16px" : "0"};">
        <span class="veredicto-badge veredicto-badge--${candidatura.estado_candidatura === "oficializado" ? "viable_y_en_plan" : "pendiente"}">${candidatura.estado_candidatura === "oficializado" ? "Candidatura oficializada" : "Candidatura inscrita (pendiente de oficialización)"}</span>
        <span class="veredicto-badge veredicto-badge--incomprobable">Jurisdicción DPA ${escapeHtml(candidatura.jurisdiccion_dpa)}</span>
        ${sinPlan ? `<span class="veredicto-badge veredicto-badge--informacion_falsa">No registró plan de trabajo ante el CNE</span>` : ""}
      </div>
      ${sinPlan
        ? `<p style="color:var(--color-text-muted); font-size: 13px;">Sin un plan registrado, la contrastación solo puede evaluarse contra las competencias legales del COOTAD, no contra propuestas propias.</p>`
        : ""
      }
      <button type="button" class="btn-primary" id="btn-contrastar" style="margin-top:16px;">
        Contrastar una declaración de este candidato
      </button>
    </div>

    <section class="dash-section">
      <div class="dash-section__header">
        <h2 class="dash-section__title">Propuestas del plan de trabajo</h2>
      </div>
      <div id="candidato-propuestas">
        ${
          (candidatura.propuestas || []).length
            ? candidatura.propuestas.map(propuestaHtml).join("")
            : `<div class="console-card" style="text-align:center; padding: 24px;">
                 <p style="color:var(--color-text-muted);">Todavía no hay propuestas cargadas del plan de trabajo de esta candidatura.</p>
               </div>`
        }
      </div>
    </section>

    <section class="dash-section">
      <div class="dash-section__header">
        <h2 class="dash-section__title">Informes publicados</h2>
      </div>
      <div id="candidato-informes">
        ${
          (candidatura.informes_publicados || []).length
            ? `<div class="recent-grid">${candidatura.informes_publicados.map(informeHtml).join("")}</div>`
            : `<div class="console-card" style="text-align:center; padding: 24px;">
                 <p style="color:var(--color-text-muted);">Todavía no hay informes de contrastación publicados sobre esta candidatura.</p>
               </div>`
        }
      </div>
    </section>
  `;

  container.querySelector("#btn-contrastar").addEventListener("click", () => {
    guardarCandidaturaPreseleccionada({ id: candidatura.id, nombre: nombres });
    location.hash = "#/";
  });
}

function propuestaHtml(texto) {
  const html = window.marked ? window.marked.parse(texto || "") : escapeHtml(texto);
  return `
    <div class="console-card" style="margin-bottom: 12px;">
      ${html}
    </div>
  `;
}

function informeHtml(informe) {
  return `
    <div class="recent-card" style="cursor:default;">
      <div class="recent-card__top">
        <span class="recent-card__cand">Declaración</span>
        ${veredictoBadgeHtml(informe.veredicto)}
      </div>
      <p class="recent-card__text">${escapeHtml(informe.afirmacion)}</p>
      <div class="recent-card__bottom">
        <span>${informe.publicado_en ? formatearFecha(informe.publicado_en) : ""}</span>
        ${
          informe.factibilidad_score != null
            ? `<span>Factibilidad: ${escapeHtml(String(informe.factibilidad_score))}</span>`
            : ""
        }
      </div>
    </div>
  `;
}

function errorHtml(mensaje) {
  return `
    <div class="console-card" style="text-align:center; padding: 32px 24px;">
      <p style="color:var(--veredicto-falso-text);">${escapeHtml(mensaje)}</p>
      <a href="#/candidatos" class="recent-card__cta" style="display:inline-block; margin-top:12px;">← Volver a candidatos</a>
    </div>
  `;
}
