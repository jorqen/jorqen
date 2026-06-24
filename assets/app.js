const LANGUAGE_STORAGE_KEY = "jorqen.language";
const THEME_STORAGE_KEY = "jorqen.theme";
const SUPPORTED_THEMES = ["light", "dark"];
const APP_SCRIPT_URL = detectAppScriptUrl();
const SITE_BASE_PATH = detectSiteBasePath(APP_SCRIPT_URL);
const ASSET_ROOT = `${SITE_BASE_PATH.replace(/\/$/, "")}/assets`;
const MEDIA_ROOT = `${ASSET_ROOT}/media`;

let SITE_URL = normalizeSiteUrl(window.location.origin);
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
  THEMED_MEDIA_FILES = new Set(source.site.media.themedFiles);
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

async function loadPageData() {
  const embeddedData = document.getElementById("resume-data");
  if (!embeddedData?.textContent) {
    throw new Error("Embedded resume data is not available.");
  }
  return JSON.parse(embeddedData.textContent);
}

function mediaPath(fileName, theme = "") {
  return theme && THEMED_MEDIA_FILES.has(fileName)
    ? `${MEDIA_ROOT}/${theme}/${fileName}`
    : `${MEDIA_ROOT}/${fileName}`;
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

async function init() {
  const source = await loadPageData();
  const theme = detectTheme();

  configureSite(source);
  setTheme(theme);
  updateHeaderOffset();
  updateThemeSwitcher(source, theme);
  setupThemeSwitcher(source);
  setupSystemThemeListener(source);
  setupLanguagePreference(source);
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
