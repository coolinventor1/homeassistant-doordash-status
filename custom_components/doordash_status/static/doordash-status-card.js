const CARD_TAG_NAME = "doordash-latest-order-card";

const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

const fireEvent = (node, type, detail, options) => {
  const event = new Event(type, {
    bubbles: options?.bubbles ?? true,
    cancelable: options?.cancelable ?? false,
    composed: options?.composed ?? true,
  });
  event.detail = detail;
  node.dispatchEvent(event);
  return event;
};

const buildInitials = (value) => {
  const cleaned = String(value ?? "").trim();
  if (!cleaned) {
    return "DD";
  }
  const parts = cleaned.split(/\s+/).slice(0, 2);
  return parts.map((part) => part.charAt(0).toUpperCase()).join("") || "DD";
};

const normalizePreviewItems = (value) => {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      name: typeof item.name === "string" ? item.name.trim() : "",
      image_url: typeof item.image_url === "string" ? item.image_url.trim() : "",
      quantity: Number.isFinite(Number(item.quantity)) ? Number(item.quantity) : null,
    }))
    .filter((item) => item.image_url);
};

const resolveCardData = (stateObj) => {
  const attrs = stateObj?.attributes ?? {};
  const latestOrder = attrs.latest_order && typeof attrs.latest_order === "object"
    ? attrs.latest_order
    : {};

  const storeName =
    attrs.store_name ||
    (typeof stateObj?.state === "string" && stateObj.state !== "unknown" && stateObj.state !== "unavailable"
      ? stateObj.state
      : "") ||
    latestOrder.store_name ||
    "Latest DoorDash order";

  const totalDisplay = attrs.total_display || latestOrder.total_display || "";
  const status = attrs.status || latestOrder.status || "";
  const storeImageUrl = attrs.store_image_url || latestOrder.store_image_url || "";
  const itemCount =
    Number(attrs.item_count ?? latestOrder.item_count ?? 0) ||
    normalizePreviewItems(attrs.items_preview).length;
  const itemsPreview = normalizePreviewItems(attrs.items_preview);

  return {
    storeName,
    totalDisplay,
    status,
    storeImageUrl,
    itemCount,
    itemsPreview,
  };
};

class DoorDashLatestOrderCard extends HTMLElement {
  static getStubConfig() {
    return {
      entity: "",
    };
  }

  static get properties() {
    return {
      hass: {},
      _config: {},
    };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._hass = null;
  }

  setConfig(config) {
    if (!config?.entity || typeof config.entity !== "string") {
      throw new Error("You need to define an entity for the DoorDash card.");
    }
    this._config = {
      title: "Latest order",
      ...config,
    };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return {
      rows: 4,
      columns: 6,
      min_rows: 3,
      min_columns: 4,
    };
  }

  _handleActivate() {
    if (!this._config?.entity) {
      return;
    }
    fireEvent(this, "hass-more-info", { entityId: this._config.entity });
  }

  _render() {
    if (!this.shadowRoot || !this._config) {
      return;
    }

    const stateObj = this._hass?.states?.[this._config.entity];
    if (!stateObj) {
      this.shadowRoot.innerHTML = `
        <style>${this._styles()}</style>
        <ha-card>
          <div class="wrapper empty">
            <div>
              <p class="eyebrow">${escapeHtml(this._config.title || "Latest order")}</p>
              <h2 class="store-name">DoorDash</h2>
              <p class="meta">The configured entity is unavailable.</p>
            </div>
          </div>
        </ha-card>
      `;
      return;
    }

    const { storeName, totalDisplay, status, storeImageUrl, itemCount, itemsPreview } = resolveCardData(stateObj);
    const subtitleBits = [status, itemCount ? `${itemCount} item${itemCount === 1 ? "" : "s"}` : ""].filter(Boolean);
    const previewMarkup = itemsPreview.length
      ? itemsPreview
          .map(
            (item) => `
              <div class="thumb" title="${escapeHtml(item.name || "Item")}">
                <img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.name || "Item")}" loading="lazy" />
              </div>
            `,
          )
          .join("")
      : `<div class="thumb thumb-placeholder">${escapeHtml(buildInitials(storeName))}</div>`;

    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card tabindex="0" role="button" aria-label="${escapeHtml(storeName)}" class="card-shell">
        <div class="wrapper">
          <div class="header-row">
            <div class="brand-wrap">
              ${
                storeImageUrl
                  ? `<img class="brand-image" src="${escapeHtml(storeImageUrl)}" alt="${escapeHtml(storeName)}" loading="lazy" />`
                  : `<div class="brand-fallback">${escapeHtml(buildInitials(storeName))}</div>`
              }
              <div class="copy">
                <p class="eyebrow">${escapeHtml(this._config.title || "Latest order")}</p>
                <h2 class="store-name">${escapeHtml(storeName)}</h2>
                ${
                  subtitleBits.length
                    ? `<p class="meta">${escapeHtml(subtitleBits.join(" · "))}</p>`
                    : ""
                }
              </div>
            </div>
            ${
              totalDisplay
                ? `<div class="total-pill">${escapeHtml(totalDisplay)}</div>`
                : ""
            }
          </div>
          <div class="thumb-row">${previewMarkup}</div>
        </div>
      </ha-card>
    `;

    const card = this.shadowRoot.querySelector("ha-card");
    if (card) {
      card.addEventListener("click", () => this._handleActivate());
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          this._handleActivate();
        }
      });
    }
  }

  _styles() {
    return `
      :host {
        display: block;
      }

      ha-card {
        cursor: pointer;
        border-radius: 24px;
        overflow: hidden;
      }

      .wrapper {
        padding: 18px;
        display: grid;
        gap: 18px;
      }

      .wrapper.empty {
        min-height: 120px;
        align-items: center;
      }

      .header-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 14px;
      }

      .brand-wrap {
        min-width: 0;
        display: flex;
        align-items: center;
        gap: 14px;
      }

      .brand-image,
      .brand-fallback {
        width: 56px;
        height: 56px;
        border-radius: 18px;
        flex: 0 0 auto;
        border: 1px solid rgba(17, 17, 17, 0.08);
        background: #f6f6f6;
      }

      .brand-image {
        object-fit: cover;
      }

      .brand-fallback {
        display: grid;
        place-items: center;
        font-size: 0.88rem;
        font-weight: 600;
        color: #111;
      }

      .copy {
        min-width: 0;
        display: grid;
        gap: 4px;
      }

      .eyebrow,
      .meta {
        margin: 0;
        color: var(--secondary-text-color);
        font-size: 0.82rem;
        line-height: 1.35;
      }

      .store-name {
        margin: 0;
        color: var(--primary-text-color);
        font-size: 1.32rem;
        line-height: 1.15;
        font-weight: 500;
        overflow-wrap: anywhere;
      }

      .total-pill {
        flex: 0 0 auto;
        padding: 9px 12px;
        border-radius: 999px;
        background: #111;
        color: #fff;
        font-size: 0.96rem;
        line-height: 1;
        font-weight: 500;
        white-space: nowrap;
      }

      .thumb-row {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
      }

      .thumb,
      .thumb-placeholder {
        width: 52px;
        height: 52px;
        border-radius: 16px;
        overflow: hidden;
        border: 1px solid rgba(17, 17, 17, 0.08);
        background: #f6f6f6;
      }

      .thumb img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
      }

      .thumb-placeholder {
        display: grid;
        place-items: center;
        color: #111;
        font-size: 0.78rem;
        font-weight: 600;
      }
    `;
  }
}

if (!customElements.get(CARD_TAG_NAME)) {
  customElements.define(CARD_TAG_NAME, DoorDashLatestOrderCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_TAG_NAME,
  name: "DoorDash Latest Order Card",
  description: "Shows the latest DoorDash order with store art, total, and item thumbnails.",
});
