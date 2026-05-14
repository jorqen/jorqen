const LANGUAGE_STORAGE_KEY = "jorqen.language";
const THEME_STORAGE_KEY = "jorqen.theme";
const APP_SCRIPT_URL = detectAppScriptUrl();
const SITE_BASE_PATH = detectSiteBasePath(APP_SCRIPT_URL);
const ASSET_ROOT = `${SITE_BASE_PATH.replace(/\/$/, "")}/assets`;
const MEDIA_ROOT = `${ASSET_ROOT}/media`;
const SITE_URL_PLACEHOLDER = "${SITE_URL}";
let SITE_URL = normalizeSiteUrl(window.location.origin);
const RESPONSIVE_IMAGE_WIDTHS = [480, 720, 1080];
const RESPONSIVE_IMAGE_FORMATS = ["avif", "webp"];
const RESPONSIVE_IMAGE_EXTENSIONS = new Set(["jpg", "jpeg", "png"]);
const SUPPORTED_THEMES = ["light", "dark"];
let trackedPageViewUrl = "";
const THEMED_MEDIA_FILES = new Set([
  "briefcase.svg",
  "contact.svg",
  "download.svg",
  "education.svg",
  "exnode.svg",
  "external-link.svg",
  "github.svg",
  "layers.svg",
  "location.svg",
  "moon.svg",
  "sbertech.svg",
  "star.svg",
  "sun.svg",
  "vstu.svg",
]);
const CONTACT_ANALYTICS_GOALS = {
  email: "email_click",
  github: "github_click",
  linkedin: "linkedin_click",
  telegram: "telegram_click",
};

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

function sitePath(path) {
  const value = String(path || "/");
  if (!value.startsWith("/") || /^[a-z][a-z\d+\-.]*:/i.test(value)) {
    return value;
  }
  return `${SITE_BASE_PATH.replace(/\/$/, "")}${value}`;
}

function normalizeSiteUrl(value) {
  const url = new URL(String(value || ""), window.location.origin);
  url.hash = "";
  url.search = "";
  return url.href.replace(/\/$/, "");
}

function absoluteSiteUrl(path = "/") {
  if (window.JorqenAnalytics?.absoluteSiteUrl) {
    return window.JorqenAnalytics.absoluteSiteUrl(path);
  }

  return new URL(path, `${SITE_URL}/`).href;
}

function resolveConfiguredValue(value) {
  return String(value || "").replaceAll(SITE_URL_PLACEHOLDER, SITE_URL);
}

function configureSite(source) {
  SITE_URL = normalizeSiteUrl(source.site.url);
  source.contacts.website.value = SITE_URL;
  if (window.JorqenAnalytics?.configure) {
    window.JorqenAnalytics.configure({
      siteUrl: SITE_URL,
      yandexMetrikaId: source.analytics.yandexMetrikaId,
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
    const tracked = window.JorqenAnalytics.trackInitialPageView();
    if (tracked || window.JorqenAnalytics.COUNTER_ID) {
      trackedPageViewUrl = window.location.href;
    }
  }
}

function trackRoutePageView(referer = "") {
  const nextUrl = window.location.href;
  if (!window.JorqenAnalytics?.trackPageView || nextUrl === trackedPageViewUrl) {
    return;
  }

  window.JorqenAnalytics.trackPageView({
    url: nextUrl,
    title: document.title,
    referer: referer || trackedPageViewUrl || document.referrer,
  });
  trackedPageViewUrl = nextUrl;
}

function trackPhotoClick(element) {
  if (window.JorqenAnalytics?.trackPhotoClick) {
    window.JorqenAnalytics.trackPhotoClick(element);
  }
}
function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function localized(value, lang, languages) {
  if (isPlainObject(value)) {
    const keys = Object.keys(value);
    const isLocalizedMap = keys.length > 0 && keys.every((key) => languages.includes(key));
    if (isLocalizedMap) {
      return value[lang];
    }

    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, localized(item, lang, languages)]));
  }

  if (Array.isArray(value)) {
    return value.map((item) => localized(item, lang, languages));
  }

  return value;
}

function parseResumeDate(value, upperBound) {
  const [yearPart, monthPart, dayPart] = String(value).split("-");
  const year = Number(yearPart);
  const month = monthPart ? Number(monthPart) : upperBound ? 12 : 1;
  const day = dayPart && !upperBound ? Number(dayPart) : upperBound ? new Date(year, month, 0).getDate() : 1;
  return new Date(year, month - 1, day);
}

function formatResumeDate(value, lang) {
  const parts = String(value).split("-");
  if (parts.length === 1) {
    return parts[0];
  }

  return new Intl.DateTimeFormat(lang, { month: "short", year: "numeric" }).format(
    parseResumeDate(value, false),
  );
}

function formatPeriod(item, labels, lang) {
  const start = formatResumeDate(item.startDate, lang);
  const end = item.endDate ? formatResumeDate(item.endDate, lang) : labels.present;
  return `${start} - ${end}`;
}

function resumeDateUpperBound(value) {
  return value ? parseResumeDate(value, true) : null;
}

function isExpectedEducation(item) {
  const endDate = resumeDateUpperBound(item.endDate);
  return endDate ? endDate > new Date() : false;
}

function formatEducationPeriod(item, labels, lang) {
  const start = formatResumeDate(item.startDate, lang);
  const endDate = formatResumeDate(item.endDate, lang);
  const end = isExpectedEducation(item) ? `${labels.expectedGraduation}: ${endDate}` : endDate;
  return `${start} - ${end}`;
}

function fileBaseName(source) {
  return String(localized(source.person.name, source.defaultLanguage, source.languages)).trim().split(/\s+/).join("_");
}

function fileName(source, extension) {
  return `${fileBaseName(source)}.${extension}`;
}

function contactHref(contact) {
  const value = resolveConfiguredValue(contact?.value).trim();
  return value.includes("@") ? `mailto:${value}` : value;
}

function contactLabel(contact) {
  return resolveConfiguredValue(contact?.value).replace(/^https?:\/\/(www\.)?/i, "").replace(/\/$/i, "");
}

async function loadResumeData() {
  const embeddedData = document.getElementById("resume-data");
  if (embeddedData?.textContent) {
    return JSON.parse(embeddedData.textContent);
  }

  throw new Error("Embedded resume data is not available.");
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = value || "";
  }
}

function getPhotoItems(person, gallery) {
  return [person.photo, ...(gallery?.items || [])];
}

function photoId(item, defaultId) {
  return String(item?.id || item?.src || defaultId || "photo")
    .replace(/\.[^.]+$/, "")
    .replace(/[^a-z0-9]+/gi, "_")
    .replace(/^_+|_+$/g, "")
    .toLowerCase();
}

function setPhotoTriggerAttributes(element, index, label, photoId, section) {
  if (!element) {
    return;
  }

  element.dataset.photoIndex = String(index);
  element.dataset.photoId = photoId || `photo_${index}`;
  if (section) {
    element.dataset.analyticsSection = section;
  }
  element.tabIndex = 0;
  element.setAttribute("role", "button");
  element.setAttribute("aria-label", label || "");
}

function mediaPath(fileName, theme) {
  return theme && THEMED_MEDIA_FILES.has(fileName)
    ? `${MEDIA_ROOT}/${theme}/${fileName}`
    : `${MEDIA_ROOT}/${fileName}`;
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

function responsiveVariantPath(fileName, width, extension) {
  return `${MEDIA_ROOT}/generated/${imageStem(fileName)}-${width}.${extension}`;
}

function responsiveSrcset(fileName, extension) {
  return RESPONSIVE_IMAGE_WIDTHS
    .map((width) => `${responsiveVariantPath(fileName, width, extension)} ${width}w`)
    .join(", ");
}

function ensurePictureSource(picture, type) {
  const selector = `source[type="${type}"]`;
  let source = picture.querySelector(selector);
  if (!source) {
    source = document.createElement("source");
    source.type = type;
    picture.prepend(source);
  }
  return source;
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

  const picture = image.parentElement?.tagName === "PICTURE" ? image.parentElement : null;
  if (picture) {
    RESPONSIVE_IMAGE_FORMATS.slice().reverse().forEach((extension) => {
      const source = ensurePictureSource(picture, `image/${extension}`);
      source.srcset = responsiveSrcset(fileName, extension);
      source.sizes = sizes;
    });
  }
}

function createResponsivePicture(item, cssClass, sizes) {
  const picture = document.createElement("picture");
  RESPONSIVE_IMAGE_FORMATS.forEach((extension) => {
    const source = document.createElement("source");
    source.type = `image/${extension}`;
    source.srcset = responsiveSrcset(item.src, extension);
    source.sizes = sizes;
    picture.append(source);
  });

  const image = document.createElement("img");
  image.className = cssClass;
  image.alt = item.caption || "";
  image.loading = "lazy";
  image.decoding = "async";
  setResponsivePhotoImage(image, item.src, sizes);
  picture.append(image);
  return { picture, image };
}

function setMediaImage(image, fileName, theme) {
  image.dataset.media = fileName;
  image.src = mediaPath(fileName, theme);
}

function setImageSource(selector, fileName, theme) {
  const image = document.querySelector(selector);
  if (!image) {
    return;
  }

  setMediaImage(image, fileName, theme);
}

function syncStaticThemeIcons(theme) {
  document.querySelectorAll("img[data-media]").forEach((image) => {
    setMediaImage(image, image.dataset.media, theme);
  });
}

function setLinkLabel(id, text) {
  const link = document.getElementById(id);
  if (!link) {
    return;
  }

  const span = link.querySelector("span");
  if (span) {
    span.textContent = text || "";
  } else {
    link.textContent = text || "";
  }
  link.setAttribute("aria-label", text || "");
}

function setLinkHref(id, href) {
  const link = document.getElementById(id);
  if (!link) {
    return;
  }

  if (href) {
    link.href = href;
    if (href.startsWith("mailto:") || href.startsWith("tel:")) {
      link.removeAttribute("target");
      link.removeAttribute("rel");
    } else {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
  } else {
    link.removeAttribute("href");
    link.removeAttribute("target");
    link.removeAttribute("rel");
  }
}

function setMetaContent(selector, value) {
  const element = document.querySelector(selector);
  if (element) {
    element.setAttribute("content", value);
  }
}

function setElementAttribute(selector, attribute, value) {
  const element = document.querySelector(selector);
  if (element) {
    element.setAttribute(attribute, value);
  }
}

function formatSitePath(template, values) {
  return sitePath(formatConfiguredPath(template, values));
}

function formatConfiguredPath(template, values) {
  const path = String(template || "/")
    .replaceAll("{lang}", encodeURIComponent(values.lang || ""))
    .replaceAll("{file}", encodeURIComponent(values.file || ""));
  return path.startsWith("/") ? path : `/${path}`;
}

function pagePathForLanguage(source = {}, lang) {
  return formatSitePath(
    source.site.languagePathTemplate,
    { lang: lang || document.documentElement.lang || source.defaultLanguage },
  );
}

function canonicalPathForLanguage(source = {}, lang) {
  return formatConfiguredPath(
    source.site.languagePathTemplate,
    { lang: lang || document.documentElement.lang || source.defaultLanguage },
  );
}

function downloadPathForFile(source, lang, file) {
  return formatSitePath(source.site.downloadPathTemplate, { lang, file });
}

function currentCanonicalPath(source = {}, lang) {
  return canonicalPathForLanguage(source, lang);
}

function syncLanguagePageContext(source, lang) {
  document.documentElement.dataset.pageLang = lang;
  document.documentElement.dataset.canonicalPath = canonicalPathForLanguage(source, lang);
}

function syncSiteMetadata(source = {}, lang) {
  const coverUrl = absoluteSiteUrl("/assets/og-cover-recruiter.jpg");
  const pageUrl = absoluteSiteUrl(currentCanonicalPath(source, lang));

  setElementAttribute('link[rel="canonical"]', "href", pageUrl);
  setElementAttribute('link[rel="image_src"]', "href", coverUrl);
  setMetaContent('meta[property="og:site_name"]', new URL(SITE_URL).hostname);
  setMetaContent('meta[property="og:url"]', pageUrl);
  setMetaContent('meta[property="og:image"]', coverUrl);
  setMetaContent('meta[property="og:image:url"]', coverUrl);
  setMetaContent('meta[property="og:image:secure_url"]', coverUrl);
  setMetaContent('meta[name="twitter:image"]', coverUrl);
}

function syncDocumentMetadata(source, person, lang) {
  const title = `${person.name} | ${person.headline}`;
  syncSiteMetadata(source, lang);
  document.title = title;
  setMetaContent('meta[name="description"]', person.role);
  setMetaContent('meta[property="og:title"]', title);
  setMetaContent('meta[property="og:description"]', person.role);
  setMetaContent('meta[property="og:image:alt"]', title);
  setMetaContent('meta[name="twitter:title"]', title);
  setMetaContent('meta[name="twitter:description"]', person.role);
  setMetaContent('meta[name="twitter:image:alt"]', title);
}

function detectLanguage(source) {
  const url = new URL(window.location.href);
  const normalizedPath = url.pathname.endsWith("/") ? url.pathname : `${url.pathname}/`;
  const pathLang = source.languages.find((lang) => normalizedPath === pagePathForLanguage(source, lang));
  if (pathLang) {
    return pathLang;
  }

  const pageLang = document.documentElement.dataset.pageLang || document.documentElement.lang;
  if (source.languages.includes(pageLang)) {
    return pageLang;
  }

  const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
  if (source.languages.includes(stored)) {
    return stored;
  }

  const browserLanguages = [];
  if (Array.isArray(window.navigator.languages)) {
    browserLanguages.push(...window.navigator.languages);
  }
  if (typeof window.navigator.language === "string") {
    browserLanguages.push(window.navigator.language);
  }

  const browserLanguage = browserLanguages
    .map((item) => item.toLowerCase().split("-")[0])
    .find((item) => source.languages.includes(item));
  return browserLanguage || source.defaultLanguage;
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

function setLanguageButtonsState(lang) {
  document.querySelectorAll("[data-lang-switch]").forEach((button) => {
    const isActive = button.getAttribute("data-lang-switch") === lang;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
}

function setThemeButtonsState(theme) {
  document.querySelectorAll("[data-theme-switch]").forEach((button) => {
    const isActive = button.getAttribute("data-theme-switch") === theme;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
}

function languageUrl(source, lang) {
  const url = new URL(window.location.href);
  url.pathname = pagePathForLanguage(source, lang);
  url.search = "";
  return url.href;
}

function updateLanguageUrl(source, lang) {
  const nextUrl = languageUrl(source, lang);
  if (nextUrl !== window.location.href) {
    window.history.pushState({ lang }, "", nextUrl);
  }
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

function currentTheme() {
  const theme = document.documentElement.getAttribute("data-theme");
  if (!SUPPORTED_THEMES.includes(theme)) {
    throw new Error(`Unsupported active theme: ${theme}`);
  }
  return theme;
}

function renderFacts(items, theme) {
  const container = document.getElementById("hero-facts");
  if (!container) {
    return;
  }

  container.innerHTML = "";

  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "fact-item";

    const heading = document.createElement("div");
    heading.className = "fact-heading";

    if (item.icon) {
      const icon = document.createElement("img");
      setMediaImage(icon, item.icon, theme);
      icon.alt = "";
      icon.setAttribute("aria-hidden", "true");
      icon.className = "fact-icon";
      heading.append(icon);
    }

    const label = document.createElement("p");
    label.className = "fact-label";
    label.textContent = item.label;
    heading.append(label);

    const value = document.createElement("p");
    value.className = "fact-value";
    const valueLines = String(item.value || "")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);

    if (valueLines.length > 1) {
      valueLines.forEach((line) => {
        const lineElement = document.createElement("span");
        lineElement.className = "fact-value-line";
        lineElement.textContent = line;
        value.append(lineElement);
      });
    } else {
      value.textContent = item.value || "";
    }

    card.append(heading, value);
    container.append(card);
  });
}

function renderHeroPhoto(photo, index, triggerLabel) {
  const card = document.querySelector(".hero-photo-card");
  const image = document.getElementById("hero-photo");
  const caption = document.getElementById("hero-photo-caption");

  if (!image) {
    return;
  }

  if (photo?.src) {
    setResponsivePhotoImage(image, photo.src, "(max-width: 980px) min(100vw, 340px), 360px");
  } else {
    image.src = "";
    image.removeAttribute("srcset");
    image.removeAttribute("sizes");
  }
  image.alt = photo?.caption || "";
  image.loading = "eager";
  image.decoding = "async";
  image.fetchPriority = "high";
  image.style.objectPosition = photo?.position || "";
  image.style.setProperty("--photo-filter", photo?.filter || "");

  if (caption) {
    caption.textContent = photo?.caption || "";
  }

  setPhotoTriggerAttributes(card, index, triggerLabel, "avatar", "hero");
}

function sortedExperienceItems(items) {
  return [...items].sort(
    (left, right) => resumeDateUpperBound(right.startDate) - resumeDateUpperBound(left.startDate),
  );
}

function renderExperience(section, labels, theme, lang) {
  const container = document.getElementById("experience-list");
  if (!container) {
    return;
  }

  container.innerHTML = "";

  sortedExperienceItems(section.items).forEach((item) => {
    const article = document.createElement("article");
    article.className = "timeline-item";

    const companyRow = document.createElement("div");
    companyRow.className = "timeline-company-row";

    const companyMain = document.createElement("div");
    companyMain.className = "timeline-company-main";

    let companyLink = null;
    if (item.url) {
      companyLink = document.createElement("a");
      companyLink.className = "company-link";
      companyLink.href = item.url;
      companyLink.target = "_blank";
      companyLink.rel = "noopener noreferrer";
      companyLink.textContent = item.company;
      companyLink.dataset.analyticsGoal = "project_link_click";
      companyLink.dataset.analyticsLabel = item.company;
      companyLink.dataset.analyticsSection = "experience";
    } else {
      companyLink = document.createElement("span");
      companyLink.className = "company-link";
      companyLink.textContent = item.company;
    }

    if (item.icon) {
      const icon = document.createElement("img");
      icon.className = "company-icon";
      icon.alt = `${item.company} icon`;
      setMediaImage(icon, item.icon, theme);
      companyMain.append(icon);
    }
    companyMain.append(companyLink);
    companyRow.append(companyMain);

    if (item.url) {
      const companySiteLink = document.createElement("a");
      companySiteLink.className = "company-site-link";
      companySiteLink.href = item.url;
      companySiteLink.target = "_blank";
      companySiteLink.rel = "noopener noreferrer";
      companySiteLink.dataset.analyticsGoal = "project_link_click";
      companySiteLink.dataset.analyticsLabel = item.company;
      companySiteLink.dataset.analyticsSection = "experience";

      const extIcon = document.createElement("img");
      setMediaImage(extIcon, "external-link.svg", theme);
      extIcon.alt = "";
      extIcon.setAttribute("aria-hidden", "true");

      const extLabel = document.createElement("span");
      extLabel.textContent = section.companySiteLabel;

      companySiteLink.append(extIcon, extLabel);
      companyRow.append(companySiteLink);
    }

    const role = document.createElement("h3");
    role.className = "timeline-role";
    role.textContent = item.role;

    const meta = document.createElement("p");
    meta.className = "timeline-meta";
    meta.textContent = `${formatPeriod(item, labels, lang)} · ${item.location}`;

    const intro = document.createElement("p");
    intro.className = "timeline-intro";
    intro.textContent = item.summary;

    const list = document.createElement("ul");
    list.className = "timeline-list";
    item.highlights.forEach((bullet) => {
      const li = document.createElement("li");
      li.textContent = bullet;
      list.append(li);
    });

    const stack = document.createElement("div");
    stack.className = "stack-list";
    item.stack.forEach((tech) => {
      const chip = document.createElement("span");
      chip.className = "stack-chip";
      chip.textContent = tech;
      stack.append(chip);
    });

    article.append(companyRow, role, meta, intro, list, stack);
    container.append(article);
  });
}

function renderEducation(section, labels, theme, lang) {
  const container = document.getElementById("education-list");
  if (!container) {
    return;
  }

  container.innerHTML = "";
  section.items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "education-item";

    const institutionRow = document.createElement("div");
    institutionRow.className = "timeline-company-row";

    const institution = document.createElement("h3");
    institution.className = "education-heading timeline-company-main";

    if (item.icon) {
      const icon = document.createElement("img");
      icon.className = "company-icon";
      icon.alt = `${item.institution} icon`;
      setMediaImage(icon, item.icon, theme);
      institution.append(icon);
    }

    let institutionLink = null;
    if (item.url) {
      institutionLink = document.createElement("a");
      institutionLink.className = "company-link";
      institutionLink.href = item.url;
      institutionLink.target = "_blank";
      institutionLink.rel = "noopener noreferrer";
      institutionLink.textContent = item.institution;
    } else {
      institutionLink = document.createElement("span");
      institutionLink.className = "company-link";
      institutionLink.textContent = item.institution;
    }

    institution.append(institutionLink);
    institutionRow.append(institution);

    if (item.url) {
      const institutionSiteLink = document.createElement("a");
      institutionSiteLink.className = "company-site-link";
      institutionSiteLink.href = item.url;
      institutionSiteLink.target = "_blank";
      institutionSiteLink.rel = "noopener noreferrer";

      const extIcon = document.createElement("img");
      setMediaImage(extIcon, "external-link.svg", theme);
      extIcon.alt = "";
      extIcon.setAttribute("aria-hidden", "true");

      const extLabel = document.createElement("span");
      extLabel.textContent = section.institutionSiteLabel || "University site";

      institutionSiteLink.append(extIcon, extLabel);
      institutionRow.append(institutionSiteLink);
    }

    const degree = document.createElement("p");
    degree.className = "education-degree";
    degree.textContent = item.degree;

    const meta = document.createElement("p");
    meta.className = "education-meta";
    meta.textContent = formatEducationPeriod(item, labels, lang);

    card.append(institutionRow, degree, meta);
    container.append(card);
  });
}

function renderStrengths(cards) {
  const container = document.getElementById("strengths-grid");
  if (!container) {
    return;
  }

  container.innerHTML = "";
  cards.forEach((card) => {
    const node = document.createElement("article");
    node.className = "strength-card";

    const title = document.createElement("h3");
    title.textContent = card.title;

    const text = document.createElement("p");
    text.textContent = card.body;

    node.append(title, text);
    container.append(node);
  });
}

function renderSkills(groups) {
  const container = document.getElementById("skills-grid");
  if (!container) {
    return;
  }

  container.innerHTML = "";
  groups.forEach((group) => {
    const card = document.createElement("article");
    card.className = "skill-card";

    const title = document.createElement("h3");
    title.textContent = group.title;

    const list = document.createElement("div");
    list.className = "skill-items";

    group.items.forEach((item) => {
      const chip = document.createElement("span");
      chip.className = "skill-item";
      chip.textContent = item;
      list.append(chip);
    });

    card.append(title, list);
    container.append(card);
  });
}

function renderPreferences(items) {
  const container = document.getElementById("preferences-list");
  if (!container) {
    return;
  }

  container.innerHTML = "";
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    container.append(li);
  });
}

function renderGallery(items, startIndex, triggerLabel) {
  const container = document.getElementById("gallery-grid");
  if (!container) {
    return;
  }

  container.innerHTML = "";
  items.forEach((item, offset) => {
    const card = document.createElement("article");
    card.className = "gallery-card";
    setPhotoTriggerAttributes(
      card,
      startIndex + offset,
      `${triggerLabel}: ${item.caption || ""}`,
      photoId(item, `gallery_${offset + 1}`),
      "photos",
    );

    const { picture, image } = createResponsivePicture(item, "gallery-photo", "(max-width: 640px) 46vw, 31vw");
    image.style.objectPosition = item.position || "";
    image.style.setProperty("--photo-filter", item.filter || "");

    card.append(picture);
    container.append(card);
  });
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
  const image = document.getElementById("lightbox-image");
  const caption = document.getElementById("lightbox-caption");
  const counter = document.getElementById("lightbox-counter");
  const prevButton = document.getElementById("lightbox-prev");
  const nextButton = document.getElementById("lightbox-next");
  const hasMultipleItems = state.items.length > 1;

  if (!item || !image) {
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
      slide.setAttribute("aria-label", slideItem?.caption || slide.getAttribute("aria-label") || "");
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

function syncPhotoLightboxItems(state, items) {
  state.items = items;
  if (state.index >= items.length) {
    state.index = Math.max(0, items.length - 1);
  }

  const lightbox = document.getElementById("photo-lightbox");
  if (lightbox && !lightbox.hidden) {
    renderPhotoLightbox(state);
  }
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

function syncResumeLinks(source, lang) {
  Object.entries(localized(source.siteUi.resumeDownloads.labels, lang, source.languages)).forEach(([extension, text]) => {
    const link = document.getElementById(`resume-${extension}`);
    const label = document.getElementById(`resume-${extension}-label`);
    if (!link || !label) {
      return;
    }

    const name = fileName(source, extension);
    link.href = downloadPathForFile(source, lang, name);
    link.setAttribute("download", name);
    label.textContent = text;
  });
}

function updateThemeSwitcher(source, lang, theme) {
  const labels = localized(source.siteUi.theme, lang, source.languages);
  const switcher = document.getElementById("theme-switch");

  if (switcher) {
    switcher.setAttribute("aria-label", labels.switcherLabel);
    document.querySelectorAll("[data-theme-switch]").forEach((button) => {
      const buttonTheme = button.getAttribute("data-theme-switch");
      const label = buttonTheme === "dark" ? labels.toDark : labels.toLight;
      button.setAttribute("aria-label", label);
    });
  }

  syncStaticThemeIcons(theme);
  setThemeButtonsState(theme);
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

function renderLanguage(source, state, lang) {
  const data = localized(source, lang, source.languages);
  const labels = data.resumeLabels;
  const person = data.person;
  const theme = currentTheme();
  const photoItems = getPhotoItems(person, data.gallery);

  document.documentElement.lang = lang;
  syncLanguagePageContext(source, lang);
  syncDocumentMetadata(source, person, lang);

  setText("brand-link", person.name);
  setText("nav-resume", localized(source.siteUi.navResume, lang, source.languages));
  setText("nav-experience", data.experience.title);
  setText("nav-education", data.education.title);
  setText("nav-strengths", data.strengths.title);
  setText("nav-skills", labels.stack);

  const langSwitch = document.getElementById("lang-switch");
  if (langSwitch) {
    langSwitch.setAttribute("aria-label", localized(source.siteUi.langSwitcherLabel, lang, source.languages));
  }

  setText("hero-kicker", person.headline);
  setText("hero-name", person.name);
  setText("hero-role", person.role);
  setText("hero-summary", person.summary);

  document.querySelectorAll(".hero-links .action-link").forEach((link) => {
    const key = link.id.replace(/^hero-/, "");
    const contact = data.contacts?.[key];
    const label = contactLabel(contact);
    setLinkLabel(link.id, label);
    setLinkHref(link.id, contactHref(contact));
    setImageSource(`#${link.id} img`, contact.icon, theme);
    if (link) {
      link.title = resolveConfiguredValue(contact?.value || label);
      link.dataset.analyticsGoal = CONTACT_ANALYTICS_GOALS[key] || "";
      link.dataset.analyticsContact = "true";
      link.dataset.analyticsLabel = key === "email" ? "Email" : key.charAt(0).toUpperCase() + key.slice(1);
      link.dataset.analyticsSection = "contacts";
    }
  });

  const lightboxLabels = localized(source.siteUi.lightbox, lang, source.languages);
  renderFacts(person.facts, theme);
  renderHeroPhoto(person.photo, 0, `${lightboxLabels.openPhoto}: ${person.photo.caption || ""}`);

  setText("resume-title", localized(source.siteUi.resumeDownloads.title, lang, source.languages));
  syncResumeLinks(source, lang);

  setText("experience-title", data.experience.title);
  renderExperience(data.experience, labels, theme, lang);

  setText("education-title", data.education.title);
  setText("education-subtitle", data.education.subtitle);
  renderEducation(data.education, labels, theme, lang);

  setText("strengths-title", data.strengths.title);
  setText("strengths-subtitle", data.strengths.subtitle);
  renderStrengths(data.strengths.cards);

  setText("skills-title", data.skills.title);
  renderSkills(data.skills.groups);

  setText("preferences-title", data.preferences.title);
  renderPreferences(data.preferences.items);

  setText("photos-title", data.gallery.title);
  setText("photos-subtitle", data.gallery.subtitle);
  renderGallery(data.gallery.items, 1, lightboxLabels.openPhoto);
  updatePhotoLightboxLabels(lightboxLabels);
  syncPhotoLightboxItems(state, photoItems);

  setText(
    "footer-text",
    localized(source.siteUi.footer, lang, source.languages)
      .replace("{name}", person.name)
      .replace("{year}", String(new Date().getFullYear())),
  );

  setLanguageButtonsState(lang);
  updateThemeSwitcher(source, lang, theme);
  updateHeaderOffset();
  refreshAnalyticsTracking();
}

function applyLanguage(source, state, lang) {
  if (!source.languages.includes(lang)) {
    throw new Error(`Unsupported language: ${lang}`);
  }

  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, lang);
  renderLanguage(source, state, lang);
}

function setupLanguageSwitcher(source, state) {
  document.querySelectorAll("[data-lang-switch]").forEach((button) => {
    button.addEventListener("click", (event) => {
      if (button instanceof HTMLAnchorElement) {
        event.preventDefault();
      }
      const nextLang = button.getAttribute("data-lang-switch");
      if (!source.languages.includes(nextLang)) {
        return;
      }
      const previousUrl = window.location.href;
      updateLanguageUrl(source, nextLang);
      applyLanguage(source, state, nextLang);
      trackRoutePageView(previousUrl);
    });
  });
}

function setupLanguageHistory(source, state) {
  window.addEventListener("popstate", () => {
    const previousUrl = trackedPageViewUrl || document.referrer;
    applyLanguage(source, state, detectLanguage(source));
    trackRoutePageView(previousUrl);
  });
}

function setupThemeSwitcher(source, state) {
  document.querySelectorAll("[data-theme-switch]").forEach((button) => {
    button.addEventListener("click", () => {
      const nextTheme = button.getAttribute("data-theme-switch");
      setTheme(nextTheme, true);

      const currentLang = document.documentElement.lang || detectLanguage(source);
      renderLanguage(source, state, currentLang);
      updateHeaderOffset();
    });
  });
}

function setupSystemThemeListener(source, state) {
  const query = window.matchMedia?.("(prefers-color-scheme: dark)");
  if (!query) {
    return;
  }

  const handleChange = (event) => {
    if (hasStoredTheme()) {
      return;
    }
    setTheme(event.matches ? "dark" : "light");
    renderLanguage(source, state, document.documentElement.lang || detectLanguage(source));
  };

  if (query.addEventListener) {
    query.addEventListener("change", handleChange);
  } else if (query.addListener) {
    query.addListener(handleChange);
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
  const source = await loadResumeData();
  const lightboxState = { items: [], index: 0, opener: null };

  configureSite(source);
  const lang = detectLanguage(source);
  const theme = detectTheme();

  setTheme(theme);
  updateHeaderOffset();
  setupLanguageSwitcher(source, lightboxState);
  setupLanguageHistory(source, lightboxState);
  setupThemeSwitcher(source, lightboxState);
  setupSystemThemeListener(source, lightboxState);
  setupPhotoLightbox(lightboxState);
  window.addEventListener("resize", updateHeaderOffset);
  window.addEventListener("load", updateHeaderOffset);
  applyLanguage(source, lightboxState, lang);
  trackInitialPageView();
}

function renderInitError(error) {
  console.error(error);
  document.documentElement.lang = "en";
  setTheme(detectTheme());
  setText("hero-kicker", "Resume data unavailable");
  setText("hero-name", "Cannot load resume data");
  setText(
    "hero-summary",
    "Run a local HTTP server from the project root and open http://localhost:8000/.",
  );
  updateHeaderOffset();
}

init().catch(renderInitError);
