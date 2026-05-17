const LANGUAGE_STORAGE_KEY = "jorqen.language";
const THEME_STORAGE_KEY = "jorqen.theme";
const SUPPORTED_THEMES = ["light", "dark"];
const APP_SCRIPT_URL = detectAppScriptUrl();
const SITE_BASE_PATH = detectSiteBasePath(APP_SCRIPT_URL);
const ASSET_ROOT = `${SITE_BASE_PATH.replace(/\/$/, "")}/assets`;
const MEDIA_ROOT = `${ASSET_ROOT}/media`;

let SITE_URL = normalizeSiteUrl(window.location.origin);
let RESPONSIVE_IMAGE_DIR = "";
let RESPONSIVE_IMAGE_WIDTHS = [];
let RESPONSIVE_IMAGE_FORMATS = [];
let RESPONSIVE_IMAGE_EXTENSIONS = new Set();
let THEMED_MEDIA_FILES = new Set();

function detectAppScriptUrl() {
  if (!(document.currentScript instanceof HTMLScriptElement)) {
    throw new Error("Cannot detect app script URL.");
  }
  return new URL(document.currentScript.src);
}

function detectSiteBasePath(scriptUrl) {
  const marker = "/assets/app.js";
  const markerIndex = scriptUrl.pathname.lastIndexOf(marker);
  if (markerIndex < 0) {
    throw new Error(`App script must be served from ${marker}.`);
  }
  return `${scriptUrl.pathname.slice(0, markerIndex)}/`;
}

function normalizeSiteUrl(value) {
  const url = new URL(String(value || ""), window.location.origin);
  url.hash = "";
  url.search = "";
  return url.href.replace(/\/$/, "");
}

function configureMedia(source) {
  const media = source.site.media;
  const responsive = media.responsive;

  RESPONSIVE_IMAGE_DIR = responsive.directory;
  RESPONSIVE_IMAGE_WIDTHS = responsive.widths.map((width) => Number(width));
  RESPONSIVE_IMAGE_FORMATS = responsive.formats.map((format) => String(format).toLowerCase());
  RESPONSIVE_IMAGE_EXTENSIONS = new Set(
    responsive.sourceExtensions.map((extension) => String(extension).replace(/^\./, "").toLowerCase()),
  );
  THEMED_MEDIA_FILES = new Set(media.themedFiles);
}

function configureSite(source) {
  SITE_URL = normalizeSiteUrl(source.site.url);
  configureMedia(source);

  if (window.JorqenAnalytics?.configure) {
    window.JorqenAnalytics.configure({
      siteUrl: SITE_URL,
      yandexMetrikaId: source.analytics.yandexMetrikaId,
      yandexMetrikaOrigin: source.analytics.yandexMetrikaOrigin,
    });
  }
}

function refreshAnalyticsTracking() {
  if (window.JorqenAnalytics?.initPageTracking) {
    window.JorqenAnalytics.initPageTracking();
  }
}

function trackInitialPageView() {
  if (window.JorqenAnalytics?.trackInitialPageView) {
    window.JorqenAnalytics.trackInitialPageView();
  }
}

function trackPhotoClick(element) {
  if (window.JorqenAnalytics?.trackPhotoClick) {
    window.JorqenAnalytics.trackPhotoClick(element);
  }
}

async function loadPageData() {
  const embeddedData = document.getElementById("resume-data");
  if (!embeddedData?.textContent) {
    throw new Error("Embedded resume data is not available.");
  }
  return JSON.parse(embeddedData.textContent);
}

function imageExtension(fileName) {
  return String(fileName || "").split(".").pop().toLowerCase();
}

function imageStem(fileName) {
  return String(fileName || "")
    .replace(/\.[^.]+$/, "")
    .replace(/[^a-z0-9]+/gi, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
}

function hasResponsiveVariants(fileName) {
  return RESPONSIVE_IMAGE_EXTENSIONS.has(imageExtension(fileName));
}

function mediaPath(fileName, theme = "") {
  return theme && THEMED_MEDIA_FILES.has(fileName)
    ? `${MEDIA_ROOT}/${theme}/${fileName}`
    : `${MEDIA_ROOT}/${fileName}`;
}

function responsiveVariantPath(fileName, width, extension) {
  return `${MEDIA_ROOT}/${RESPONSIVE_IMAGE_DIR}/${imageStem(fileName)}-${width}.${extension}`;
}

function responsiveSrcset(fileName, extension) {
  return RESPONSIVE_IMAGE_WIDTHS
    .map((width) => `${responsiveVariantPath(fileName, width, extension)} ${width}w`)
    .join(", ");
}

function setResponsivePhotoImage(image, fileName, sizes) {
  if (!image || !fileName) {
    return;
  }

  image.src = mediaPath(fileName);
  if (!hasResponsiveVariants(fileName)) {
    image.removeAttribute("srcset");
    image.removeAttribute("sizes");
    return;
  }

  image.srcset = responsiveSrcset(fileName, "webp");
  image.sizes = sizes;
}

function syncStaticThemeIcons(theme) {
  document.querySelectorAll("img[data-media]").forEach((image) => {
    image.src = mediaPath(image.dataset.media, theme);
  });
}

function detectTheme() {
  const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (SUPPORTED_THEMES.includes(storedTheme)) {
    return storedTheme;
  }

  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function hasStoredTheme() {
  return SUPPORTED_THEMES.includes(window.localStorage.getItem(THEME_STORAGE_KEY));
}

function setTheme(theme, persist = false) {
  if (!SUPPORTED_THEMES.includes(theme)) {
    throw new Error(`Unsupported theme: ${theme}`);
  }
  document.documentElement.setAttribute("data-theme", theme);

  if (persist) {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }
}

function setThemeButtonsState(theme) {
  document.querySelectorAll("[data-theme-switch]").forEach((button) => {
    const isActive = button.getAttribute("data-theme-switch") === theme;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
}

function updateThemeSwitcher(source, theme) {
  const labels = source.theme;
  const switcher = document.getElementById("theme-switch");

  if (switcher) {
    switcher.setAttribute("aria-label", labels.switcherLabel);
    document.querySelectorAll("[data-theme-switch]").forEach((button) => {
      const buttonTheme = button.getAttribute("data-theme-switch");
      button.setAttribute("aria-label", buttonTheme === "dark" ? labels.toDark : labels.toLight);
    });
  }

  syncStaticThemeIcons(theme);
  setThemeButtonsState(theme);
}

function setupThemeSwitcher(source) {
  document.querySelectorAll("[data-theme-switch]").forEach((button) => {
    button.addEventListener("click", () => {
      const nextTheme = button.getAttribute("data-theme-switch");
      setTheme(nextTheme, true);
      updateThemeSwitcher(source, nextTheme);
    });
  });
}

function setupSystemThemeListener(source) {
  const query = window.matchMedia?.("(prefers-color-scheme: dark)");
  if (!query) {
    return;
  }

  const handleChange = (event) => {
    if (hasStoredTheme()) {
      return;
    }
    const nextTheme = event.matches ? "dark" : "light";
    setTheme(nextTheme);
    updateThemeSwitcher(source, nextTheme);
  };

  if (query.addEventListener) {
    query.addEventListener("change", handleChange);
  } else if (query.addListener) {
    query.addListener(handleChange);
  }
}

function setupLanguagePreference(source) {
  const currentLang = source.lang || document.documentElement.lang;
  document.querySelectorAll("[data-lang-switch]").forEach((link) => {
    const lang = link.getAttribute("data-lang-switch");
    const isActive = lang === currentLang;
    link.classList.toggle("active", isActive);
    link.setAttribute("aria-pressed", String(isActive));

    link.addEventListener("click", (event) => {
      if (!source.languages.includes(lang)) {
        return;
      }

      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, lang);

      if (!(link instanceof HTMLAnchorElement)) {
        return;
      }

      const targetUrl = new URL(link.href);
      targetUrl.hash = window.location.hash;
      targetUrl.search = "";
      if (targetUrl.href === window.location.href) {
        event.preventDefault();
        return;
      }
      link.href = targetUrl.href;
    });
  });
}

function updateHeaderOffset() {
  const header = document.querySelector(".site-header");
  if (!header) {
    return;
  }

  const styles = window.getComputedStyle(header);
  const stickyTop = Number.parseFloat(styles.top) || 0;
  const marginTop = Number.parseFloat(styles.marginTop) || 0;
  const offset = Math.ceil(header.offsetHeight + stickyTop + marginTop + 8);
  document.documentElement.style.setProperty("--header-offset", `${offset}px`);
}

function normalizePhotoIndex(length, index) {
  if (!length) {
    return 0;
  }

  const safeIndex = Number.isFinite(index) ? Math.trunc(index) : 0;
  return ((safeIndex % length) + length) % length;
}

function lightboxItemAt(state, index) {
  return state.items[normalizePhotoIndex(state.items.length, index)];
}

function setLightboxImage(image, item, isCurrent) {
  if (!image) {
    return;
  }

  if (!item?.src) {
    image.removeAttribute("src");
    image.removeAttribute("srcset");
    image.removeAttribute("sizes");
    image.alt = "";
    return;
  }

  setResponsivePhotoImage(
    image,
    item.src,
    isCurrent ? "(max-width: 640px) 92vw, 68vw" : "(max-width: 640px) 46vw, 22vw",
  );
  image.alt = isCurrent ? item.caption || "" : "";
  image.style.objectPosition = item.position || "";
  image.style.setProperty("--photo-filter", item.filter || "");
}

function animateLightboxStage(state, stage, direction) {
  if (!stage || !direction) {
    return;
  }

  const className = direction > 0 ? "is-moving-next" : "is-moving-prev";
  stage.classList.remove("is-moving-next", "is-moving-prev");
  void stage.offsetWidth;
  stage.classList.add(className);
  window.clearTimeout(state.lightboxMotionTimer);
  state.lightboxMotionTimer = window.setTimeout(() => {
    stage.classList.remove(className);
  }, 280);
}

function renderPhotoLightbox(state, direction = 0) {
  const item = state.items[state.index];
  const stage = document.getElementById("lightbox-stage");
  const caption = document.getElementById("lightbox-caption");
  const counter = document.getElementById("lightbox-counter");
  const prevButton = document.getElementById("lightbox-prev");
  const nextButton = document.getElementById("lightbox-next");
  const hasMultipleItems = state.items.length > 1;

  if (!item) {
    return;
  }

  document.querySelectorAll("[data-lightbox-slide]").forEach((slide) => {
    const role = slide.getAttribute("data-lightbox-slide");
    const offset = role === "prev" ? -1 : role === "next" ? 1 : 0;
    const slideIndex = normalizePhotoIndex(state.items.length, state.index + offset);
    const slideItem = lightboxItemAt(state, slideIndex);
    const isCurrent = role === "current";
    const slideImage = slide.querySelector("img");

    slide.hidden = !isCurrent && !hasMultipleItems;
    slide.dataset.photoIndex = String(slideIndex);
    if (slide instanceof HTMLButtonElement) {
      slide.disabled = !hasMultipleItems;
      const fallbackLabel = role === "prev" ? state.labels.previous : state.labels.next;
      slide.setAttribute("aria-label", slideItem?.caption || fallbackLabel);
    }
    if (isCurrent) {
      slide.removeAttribute("aria-hidden");
    } else {
      slide.setAttribute("aria-hidden", "true");
    }
    setLightboxImage(slideImage, slideItem, isCurrent);
  });

  if (caption) {
    caption.textContent = item.caption || "";
  }
  if (counter) {
    counter.textContent = `${state.index + 1} / ${state.items.length}`;
  }
  if (prevButton) {
    prevButton.disabled = !hasMultipleItems;
  }
  if (nextButton) {
    nextButton.disabled = !hasMultipleItems;
  }
  animateLightboxStage(state, stage, direction);
}

function pageSiblings() {
  return [...document.body.children].filter((element) => element.id !== "photo-lightbox");
}

function setPageInert(enabled) {
  pageSiblings().forEach((element) => {
    if (enabled) {
      if (element.hasAttribute("aria-hidden")) {
        element.dataset.previousAriaHidden = element.getAttribute("aria-hidden") || "";
      }
      element.setAttribute("aria-hidden", "true");
      element.inert = true;
      return;
    }

    element.inert = false;
    if (element.dataset.previousAriaHidden !== undefined) {
      element.setAttribute("aria-hidden", element.dataset.previousAriaHidden);
      delete element.dataset.previousAriaHidden;
    } else {
      element.removeAttribute("aria-hidden");
    }
  });
}

function focusableLightboxElements() {
  const dialog = document.getElementById("lightbox-dialog");
  if (!dialog) {
    return [];
  }
  return [...dialog.querySelectorAll(
    'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])',
  )].filter((element) => element instanceof HTMLElement && !element.hidden && element.offsetParent !== null);
}

function trapLightboxFocus(event) {
  if (event.key !== "Tab") {
    return;
  }

  const focusable = focusableLightboxElements();
  if (!focusable.length) {
    event.preventDefault();
    document.getElementById("lightbox-dialog")?.focus();
    return;
  }

  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function openPhotoLightbox(state, index, opener) {
  const lightbox = document.getElementById("photo-lightbox");
  const dialog = document.getElementById("lightbox-dialog");
  if (!lightbox || !dialog || !state.items.length) {
    return;
  }

  state.index = normalizePhotoIndex(state.items.length, index);
  state.opener = opener || null;
  if (opener instanceof Element) {
    trackPhotoClick(opener);
  }
  lightbox.hidden = false;
  setPageInert(true);
  document.body.classList.add("lightbox-open");
  renderPhotoLightbox(state);
  window.requestAnimationFrame(() => dialog.focus());
}

function closePhotoLightbox(state) {
  const lightbox = document.getElementById("photo-lightbox");
  if (!lightbox || lightbox.hidden) {
    return;
  }

  lightbox.hidden = true;
  setPageInert(false);
  document.body.classList.remove("lightbox-open");
  if (state.opener instanceof HTMLElement) {
    state.opener.focus();
  }
}

function showAdjacentPhoto(state, direction) {
  if (state.items.length <= 1) {
    return;
  }

  state.index = normalizePhotoIndex(state.items.length, state.index + direction);
  renderPhotoLightbox(state, direction);
}

function updatePhotoLightboxLabels(labels) {
  const closeButton = document.getElementById("lightbox-close");
  const closeBackdrop = document.querySelector("[data-lightbox-close]");
  const prevButton = document.getElementById("lightbox-prev");
  const nextButton = document.getElementById("lightbox-next");
  const prevPreview = document.querySelector('[data-lightbox-slide="prev"]');
  const nextPreview = document.querySelector('[data-lightbox-slide="next"]');

  if (closeButton) {
    closeButton.setAttribute("aria-label", labels.close);
  }
  if (closeBackdrop) {
    closeBackdrop.setAttribute("aria-label", labels.close);
  }
  if (prevButton) {
    prevButton.setAttribute("aria-label", labels.previous);
  }
  if (prevPreview) {
    prevPreview.setAttribute("aria-label", labels.previous);
  }
  if (nextButton) {
    nextButton.setAttribute("aria-label", labels.next);
  }
  if (nextPreview) {
    nextPreview.setAttribute("aria-label", labels.next);
  }
}

function setupPhotoLightbox(state) {
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }

    const trigger = event.target.closest("[data-photo-index]");
    if (trigger && !trigger.closest("#photo-lightbox")) {
      openPhotoLightbox(state, Number(trigger.dataset.photoIndex || "0"), trigger);
      return;
    }

    if (event.target.closest("[data-lightbox-close]") || event.target.closest("#lightbox-close")) {
      closePhotoLightbox(state);
      return;
    }

    const directionControl = event.target.closest("[data-lightbox-direction]");
    if (directionControl && directionControl.closest("#photo-lightbox")) {
      showAdjacentPhoto(state, Number(directionControl.getAttribute("data-lightbox-direction") || "0"));
    }
  });

  document.addEventListener("keydown", (event) => {
    const lightbox = document.getElementById("photo-lightbox");

    if (event.target instanceof Element) {
      const trigger = event.target.closest("[data-photo-index]");
      if (trigger && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        openPhotoLightbox(state, Number(trigger.dataset.photoIndex || "0"), trigger);
        return;
      }
    }

    if (!lightbox || lightbox.hidden) {
      return;
    }

    trapLightboxFocus(event);
    if (event.defaultPrevented) {
      return;
    }

    if (event.key === "Escape") {
      closePhotoLightbox(state);
      return;
    }

    if (event.key === "ArrowLeft") {
      showAdjacentPhoto(state, -1);
      return;
    }

    if (event.key === "ArrowRight") {
      showAdjacentPhoto(state, 1);
    }
  });
}

async function init() {
  const source = await loadPageData();
  const theme = detectTheme();
  const lightboxState = {
    items: source.lightbox.items,
    labels: source.lightbox.labels,
    index: 0,
    opener: null,
    lightboxMotionTimer: 0,
  };

  configureSite(source);
  setTheme(theme);
  updateHeaderOffset();
  updateThemeSwitcher(source, theme);
  setupThemeSwitcher(source);
  setupSystemThemeListener(source);
  setupLanguagePreference(source);
  updatePhotoLightboxLabels(source.lightbox.labels);
  setupPhotoLightbox(lightboxState);
  window.addEventListener("resize", updateHeaderOffset);
  window.addEventListener("load", updateHeaderOffset);
  refreshAnalyticsTracking();
  trackInitialPageView();
}

function renderInitError(error) {
  console.error(error);
  document.documentElement.lang = "en";
  setTheme(detectTheme());
  const kicker = document.getElementById("hero-kicker");
  const name = document.getElementById("hero-name");
  const summary = document.getElementById("hero-summary");
  if (kicker) {
    kicker.textContent = "Resume data unavailable";
  }
  if (name) {
    name.textContent = "Cannot load resume data";
  }
  if (summary) {
    summary.textContent = "Run a local HTTP server from the project root and open http://localhost:8000/.";
  }
  updateHeaderOffset();
}

init().catch(renderInitError);
