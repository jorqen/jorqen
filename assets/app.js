const LANGUAGE_STORAGE_KEY = "jorqen.language";
const THEME_STORAGE_KEY = "jorqen.theme";
const RESUME_SOURCE_URL = "resume/resume.yaml";
const MEDIA_ROOT = "assets/media";
const SUPPORTED_THEMES = ["light", "dark"];
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
const SITE_UI = {
  navResume: {
    en: "Resume",
    ru: "Резюме",
  },
  langSwitcherLabel: {
    en: "Language switcher",
    ru: "Переключение языка",
  },
  theme: {
    toDark: {
      en: "Dark theme",
      ru: "Тёмная тема",
    },
    toLight: {
      en: "Light theme",
      ru: "Светлая тема",
    },
    switcherLabel: {
      en: "Theme switcher",
      ru: "Переключение темы",
    },
  },
  resumeDownloads: {
    title: {
      en: "Download resume",
      ru: "Скачать резюме",
    },
    labels: {
      pdf: {
        en: "For sharing and printing",
        ru: "Для отправки и печати",
      },
      docx: {
        en: "Editable source",
        ru: "Редактируемый источник",
      },
      txt: {
        en: "Text version",
        ru: "Текстовая версия",
      },
    },
  },
  lightbox: {
    openPhoto: {
      en: "Open photo",
      ru: "Открыть фото",
    },
    close: {
      en: "Close photo viewer",
      ru: "Закрыть просмотр фото",
    },
    previous: {
      en: "Previous photo",
      ru: "Предыдущее фото",
    },
    next: {
      en: "Next photo",
      ru: "Следующее фото",
    },
  },
  footer: {
    en: "© {year} {name}. Personal website for recruiters and hiring managers.",
    ru: "© {year} {name}. Сайт-визитка для рекрутеров и менеджеров по найму.",
  },
};
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
  const [yearPart, monthPart] = String(value).split("-");
  const year = Number(yearPart);
  const month = monthPart ? Number(monthPart) : upperBound ? 12 : 1;
  const day = upperBound ? new Date(year, month, 0).getDate() : 1;
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
  const value = String(contact?.value || "").trim();
  return value.includes("@") ? `mailto:${value}` : value;
}

function contactLabel(contact) {
  return String(contact?.value || "").replace(/^https?:\/\/(www\.)?/i, "").replace(/\/$/i, "");
}

async function loadResumeData() {
  if (!window.jsyaml) {
    throw new Error("js-yaml is not available.");
  }

  const response = await fetch(RESUME_SOURCE_URL, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Could not load ${RESUME_SOURCE_URL}: HTTP ${response.status}.`);
  }

  return window.jsyaml.load(await response.text());
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

function setPhotoTriggerAttributes(element, index, label) {
  if (!element) {
    return;
  }

  element.dataset.photoIndex = String(index);
  element.tabIndex = 0;
  element.setAttribute("role", "button");
  element.setAttribute("aria-label", label || "");
}

function mediaPath(fileName, theme) {
  return theme && THEMED_MEDIA_FILES.has(fileName)
    ? `${MEDIA_ROOT}/${theme}/${fileName}`
    : `${MEDIA_ROOT}/${fileName}`;
}

function setMediaImage(image, fileName, theme) {
  image.dataset.media = fileName;
  image.onerror = () => {
    image.onerror = null;
    image.src = mediaPath(fileName);
  };
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
  } else {
    link.removeAttribute("href");
  }
}

function setMetaContent(selector, value) {
  const element = document.querySelector(selector);
  if (element) {
    element.setAttribute("content", value);
  }
}

function syncDocumentMetadata(person) {
  const title = `${person.name} | ${person.headline}`;
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
  const queryLang = url.searchParams.get("lang");
  if (source.languages.includes(queryLang)) {
    return queryLang;
  }

  try {
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (source.languages.includes(stored)) {
      return stored;
    }
  } catch (_error) {
    // Ignore storage access restrictions.
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
  try {
    const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (SUPPORTED_THEMES.includes(storedTheme)) {
      return storedTheme;
    }
  } catch (_error) {
    // Ignore storage access restrictions.
  }
  return "light";
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

function updateLanguageQuery(lang) {
  const url = new URL(window.location.href);
  url.searchParams.set("lang", lang);
  window.history.replaceState({}, "", url);
}

function setTheme(theme) {
  const safeTheme = SUPPORTED_THEMES.includes(theme) ? theme : "light";
  document.documentElement.setAttribute("data-theme", safeTheme);

  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, safeTheme);
  } catch (_error) {
    // Ignore storage access restrictions.
  }
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

  image.src = photo?.src ? mediaPath(photo.src) : "";
  image.style.objectPosition = photo?.position || "";
  image.style.setProperty("--photo-filter", photo?.filter || "");

  if (caption) {
    caption.textContent = photo?.caption || "";
  }

  setPhotoTriggerAttributes(card, index, triggerLabel);
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
    setPhotoTriggerAttributes(card, startIndex + offset, `${triggerLabel}: ${item.caption || ""}`);

    const image = document.createElement("img");
    image.className = "gallery-photo";
    image.src = mediaPath(item.src);
    image.loading = "lazy";
    image.style.objectPosition = item.position || "";
    image.style.setProperty("--photo-filter", item.filter || "");

    card.append(image);
    container.append(card);
  });
}

function updatePhotoLightboxLabels(labels) {
  const closeButton = document.getElementById("lightbox-close");
  const closeBackdrop = document.querySelector("[data-lightbox-close]");
  const prevButton = document.getElementById("lightbox-prev");
  const nextButton = document.getElementById("lightbox-next");

  if (closeButton) {
    closeButton.setAttribute("aria-label", labels.close);
  }
  if (closeBackdrop) {
    closeBackdrop.setAttribute("aria-label", labels.close);
  }
  if (prevButton) {
    prevButton.setAttribute("aria-label", labels.previous);
  }
  if (nextButton) {
    nextButton.setAttribute("aria-label", labels.next);
  }
}

function renderPhotoLightbox(state) {
  const item = state.items[state.index];
  const image = document.getElementById("lightbox-image");
  const caption = document.getElementById("lightbox-caption");
  const counter = document.getElementById("lightbox-counter");
  const prevButton = document.getElementById("lightbox-prev");
  const nextButton = document.getElementById("lightbox-next");

  if (!item || !image) {
    return;
  }

  image.src = mediaPath(item.src);
  image.style.setProperty("--photo-filter", item.filter || "");
  if (caption) {
    caption.textContent = item.caption || "";
  }
  if (counter) {
    counter.textContent = `${state.index + 1} / ${state.items.length}`;
  }
  if (prevButton) {
    prevButton.disabled = state.index === 0;
  }
  if (nextButton) {
    nextButton.disabled = state.index >= state.items.length - 1;
  }
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

function openPhotoLightbox(state, index, opener) {
  const lightbox = document.getElementById("photo-lightbox");
  const dialog = document.getElementById("lightbox-dialog");
  if (!lightbox || !dialog || !state.items.length) {
    return;
  }

  state.index = Math.max(0, Math.min(index, state.items.length - 1));
  state.opener = opener || null;
  lightbox.hidden = false;
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
  document.body.classList.remove("lightbox-open");
  if (state.opener instanceof HTMLElement) {
    state.opener.focus();
  }
}

function showAdjacentPhoto(state, direction) {
  const nextIndex = state.index + direction;
  if (nextIndex < 0 || nextIndex >= state.items.length) {
    return;
  }

  state.index = nextIndex;
  renderPhotoLightbox(state);
}

function syncResumeLinks(source, lang) {
  Object.entries(localized(SITE_UI.resumeDownloads.labels, lang, source.languages)).forEach(([extension, text]) => {
    const link = document.getElementById(`resume-${extension}`);
    const label = document.getElementById(`resume-${extension}-label`);
    if (!link || !label) {
      return;
    }

    const name = fileName(source, extension);
    link.href = `resume/${lang}/${name}`;
    link.setAttribute("download", name);
    label.textContent = text;
  });
}

function updateThemeSwitcher(source, lang, theme) {
  const labels = localized(SITE_UI.theme, lang, source.languages);
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
  const theme = document.documentElement.getAttribute("data-theme") || "light";
  const photoItems = getPhotoItems(person, data.gallery);

  document.documentElement.lang = lang;
  syncDocumentMetadata(person);

  setText("brand-link", person.name);
  setText("nav-resume", localized(SITE_UI.navResume, lang, source.languages));
  setText("nav-experience", data.experience.title);
  setText("nav-education", data.education.title);
  setText("nav-strengths", data.strengths.title);
  setText("nav-skills", labels.stack);

  const langSwitch = document.getElementById("lang-switch");
  if (langSwitch) {
    langSwitch.setAttribute("aria-label", localized(SITE_UI.langSwitcherLabel, lang, source.languages));
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
      link.title = String(contact?.value || label);
    }
  });

  const lightboxLabels = localized(SITE_UI.lightbox, lang, source.languages);
  renderFacts(person.facts, theme);
  renderHeroPhoto(person.photo, 0, `${lightboxLabels.openPhoto}: ${person.photo.caption || ""}`);

  setText("resume-title", localized(SITE_UI.resumeDownloads.title, lang, source.languages));
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
    localized(SITE_UI.footer, lang, source.languages)
      .replace("{name}", person.name)
      .replace("{year}", String(new Date().getFullYear())),
  );

  setLanguageButtonsState(lang);
  updateThemeSwitcher(source, lang, theme);
  updateHeaderOffset();
}

function applyLanguage(source, state, lang) {
  const safeLang = source.languages.includes(lang) ? lang : source.defaultLanguage;

  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, safeLang);
  } catch (_error) {
    // Ignore storage access restrictions.
  }

  updateLanguageQuery(safeLang);
  renderLanguage(source, state, safeLang);
}

function setupLanguageSwitcher(source, state) {
  document.querySelectorAll("[data-lang-switch]").forEach((button) => {
    button.addEventListener("click", () => {
      applyLanguage(source, state, button.getAttribute("data-lang-switch"));
    });
  });
}

function setupThemeSwitcher(source, state) {
  document.querySelectorAll("[data-theme-switch]").forEach((button) => {
    button.addEventListener("click", () => {
      const nextTheme = button.getAttribute("data-theme-switch") || "light";
      setTheme(nextTheme);

      const currentLang = document.documentElement.lang || detectLanguage(source);
      renderLanguage(source, state, currentLang);
      updateHeaderOffset();
    });
  });
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

    if (event.target.closest("#lightbox-prev")) {
      showAdjacentPhoto(state, -1);
      return;
    }

    if (event.target.closest("#lightbox-next")) {
      showAdjacentPhoto(state, 1);
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

  const lang = detectLanguage(source);
  const theme = detectTheme();

  setTheme(theme);
  updateHeaderOffset();
  setupLanguageSwitcher(source, lightboxState);
  setupThemeSwitcher(source, lightboxState);
  setupPhotoLightbox(lightboxState);
  window.addEventListener("resize", updateHeaderOffset);
  window.addEventListener("load", updateHeaderOffset);
  applyLanguage(source, lightboxState, lang);
}

function renderInitError(error) {
  console.error(error);
  document.documentElement.lang = "en";
  setTheme(detectTheme());
  setText("hero-kicker", "Resume data unavailable");
  setText("hero-name", "Cannot load resume.yaml");
  setText(
    "hero-summary",
    "Browsers block loading resume/resume.yaml from file:// URLs. Run a local HTTP server from the project root and open http://localhost:8000/.",
  );
  updateHeaderOffset();
}

init().catch(renderInitError);
