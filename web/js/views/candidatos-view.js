import { listarCandidaturas } from "../api.js";
import { escapeHtml } from "../util.js";

const ESTADO_PLAN_LABEL = {
  registrado: "Plan registrado",
  sin_plan_registrado: "Sin plan registrado ante el CNE",
};

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

export async function render(container) {
  container.innerHTML = `
    <div class="dash-hero">
      <h1 class="dash-hero__title">Candidatos</h1>
      <p class="dash-hero__subtitle">Candidaturas registradas en el corpus. Elige una para contrastar una declaración directamente sobre ella.</p>
    </div>

    <div style="margin-bottom: 20px;">
      <input
        type="search"
        class="console-input"
        id="candidatos-buscar"
        placeholder="Buscar por nombre, organización o dignidad..."
      />
    </div>

    <div id="candidatos-contenido">
      <div class="loading-state">
        <div class="apple-spinner"></div>
        <span>Cargando candidaturas...</span>
      </div>
    </div>
  `;

  const contenido = container.querySelector("#candidatos-contenido");
  const buscar = container.querySelector("#candidatos-buscar");

  let candidaturas = [];
  try {
    candidaturas = await listarCandidaturas();
  } catch {
    contenido.innerHTML = `
      <div class="console-card" style="text-align:center; padding: 32px 24px;">
        <p style="color:var(--veredicto-falso-text);">No se pudo cargar la lista de candidaturas. Intenta de nuevo.</p>
      </div>
    `;
    return;
  }

  function pintar(filtro) {
    const texto = (filtro || "").trim().toLowerCase();
    const filtradas = !texto
      ? candidaturas
      : candidaturas.filter((c) => {
          const nombres = (c.candidatos || []).map((cand) => cand.nombre).join(" ");
          const bolsa = `${nombres} ${c.organizacion_politica} ${etiquetaDignidad(c.dignidad)}`.toLowerCase();
          return bolsa.includes(texto);
        });

    if (!candidaturas.length) {
      contenido.innerHTML = `
        <div class="console-card" style="text-align:center; padding: 32px 24px;">
          <p style="color:var(--color-text-muted);">Todavía no hay candidaturas registradas en el sistema.</p>
        </div>
      `;
      return;
    }

    if (!filtradas.length) {
      contenido.innerHTML = `
        <div class="console-card" style="text-align:center; padding: 32px 24px;">
          <p style="color:var(--color-text-muted);">Ninguna candidatura coincide con "${escapeHtml(filtro)}".</p>
        </div>
      `;
      return;
    }

    contenido.innerHTML = `
      <div class="recent-grid">
        ${filtradas.map(candidaturaCardHtml).join("")}
      </div>
    `;

    contenido.querySelectorAll("[data-candidatura-id]").forEach((el) => {
      el.addEventListener("click", () => {
        location.hash = `#/candidatos/${el.dataset.candidaturaId}`;
      });
    });
  }

  buscar.addEventListener("input", () => pintar(buscar.value));
  pintar("");
}

function candidaturaCardHtml(c) {
  const candidatos = c.candidatos || [];
  const nombres = candidatos.length
    ? candidatos.map((cand) => escapeHtml(cand.nombre)).join(", ")
    : "Candidatura sin candidatos registrados";
  const sinPlan = c.estado_plan !== "registrado";

  return `
    <div class="recent-card" data-candidatura-id="${escapeHtml(String(c.id))}">
      <div class="recent-card__top">
        <span class="recent-card__cand">${nombres}</span>
        <span>
          <span class="veredicto-badge veredicto-badge--${c.estado_candidatura === "oficializado" ? "viable_y_en_plan" : "pendiente"}">${c.estado_candidatura === "oficializado" ? "Oficializada" : "Inscrita"}</span>
          ${sinPlan ? `<span class="veredicto-badge veredicto-badge--incomprobable">Sin plan</span>` : ""}
        </span>
      </div>
      <p class="recent-card__text">
        ${escapeHtml(etiquetaDignidad(c.dignidad))} — ${escapeHtml(c.organizacion_politica)} (Lista ${escapeHtml(c.lista_numero)})
      </p>
      <div class="recent-card__bottom">
        <span>${escapeHtml(ESTADO_PLAN_LABEL[c.estado_plan] || c.estado_plan)}</span>
        <span class="recent-card__cta">Ver perfil →</span>
      </div>
    </div>
  `;
}
