const SUPPORTED_LANGS = ["en", "ru"];
const LANGUAGE_STORAGE_KEY = "jorqen.language";
const THEME_STORAGE_KEY = "jorqen.theme";
const SUPPORTED_THEMES = ["light", "dark"];
const THEMED_ICON_MAP = {
  "briefcase": {
    light: "assets/icons/light/briefcase.svg",
    dark: "assets/icons/dark/briefcase.svg",
  },
  "download": {
    light: "assets/icons/light/download.svg",
    dark: "assets/icons/dark/download.svg",
  },
  "education": {
    light: "assets/icons/light/education.svg",
    dark: "assets/icons/dark/education.svg",
  },
  "external-link": {
    light: "assets/icons/light/external-link.svg",
    dark: "assets/icons/dark/external-link.svg",
  },
  "layers": {
    light: "assets/icons/light/layers.svg",
    dark: "assets/icons/dark/layers.svg",
  },
  "star": {
    light: "assets/icons/light/star.svg",
    dark: "assets/icons/dark/star.svg",
  },
  "sun": {
    light: "assets/icons/light/sun.svg",
    dark: "assets/icons/dark/sun.svg",
  },
  "moon": {
    light: "assets/icons/light/moon.svg",
    dark: "assets/icons/dark/moon.svg",
  },
};
const STATIC_THEME_ICON_BINDINGS = [
  {
    selector: '[data-theme-switch="light"] img',
    icon: "sun",
  },
  {
    selector: '[data-theme-switch="dark"] img',
    icon: "moon",
  },
  {
    selector: "#resume .panel-icon img",
    icon: "download",
  },
  {
    selector: "#experience .panel-icon img",
    icon: "briefcase",
  },
  {
    selector: "#education .panel-icon img",
    icon: "education",
  },
  {
    selector: "#strengths .panel-icon img",
    icon: "star",
  },
  {
    selector: "#skills .panel-icon img",
    icon: "layers",
  },
  {
    selector: "#preferences .panel-icon img",
    icon: "star",
  },
  {
    selector: "#photos .panel-icon img",
    icon: "star",
  },
];
const HERO_CONTACT_BINDINGS = [
  { key: "email", id: "hero-email" },
  { key: "linkedin", id: "hero-linkedin" },
  { key: "github", id: "hero-github" },
  { key: "telegram", id: "hero-telegram" },
];
const photoLightboxState = {
  items: [],
  index: 0,
  opener: null,
};

const RESUME_DATA = window.JORQEN_RESUME_DATA || { contacts: {}, content: {} };
const CONTENT = RESUME_DATA.content || {};

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = value || "";
  }
}

function getPhotoItems(data) {
  return [data.hero.photo, ...(data.gallery?.items || [])];
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

function getThemedIcon(iconPath, theme) {
  const mapping = THEMED_ICON_MAP[iconPath];
  if (!mapping) {
    return iconPath;
  }

  return mapping[theme] || iconPath;
}

function getThemeAwareSource(source, darkSource, theme) {
  return theme === "dark" && darkSource ? darkSource : source || "";
}

function setImageSource(selector, source) {
  const image = document.querySelector(selector);
  if (!image) {
    return;
  }

  if (source) {
    image.src = source;
  } else {
    image.removeAttribute("src");
  }
}

function syncStaticThemeIcons(theme) {
  STATIC_THEME_ICON_BINDINGS.forEach((binding) => {
    setImageSource(binding.selector, getThemedIcon(binding.icon, theme));
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

function getContactHref(key) {
  return RESUME_DATA.contacts?.[key]?.href || "";
}

function getContactLabel(key) {
  const value = String(RESUME_DATA.contacts?.[key]?.value || "");
  return value.replace(/^https?:\/\/(www\.)?/i, "").replace(/\/$/i, "");
}

function getContactIcon(key, theme) {
  const contact = RESUME_DATA.contacts?.[key] || {};
  return getThemeAwareSource(contact.icon, contact.iconDark, theme);
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

function detectLanguage() {
  const url = new URL(window.location.href);
  const queryLang = url.searchParams.get("lang");
  if (SUPPORTED_LANGS.includes(queryLang)) {
    return queryLang;
  }

  try {
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (SUPPORTED_LANGS.includes(stored)) {
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

  const hasRussian = browserLanguages.some((item) => item.toLowerCase().startsWith("ru"));
  return hasRussian ? "ru" : "en";
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
      icon.src = getThemeAwareSource(item.icon, item.iconDark, theme);
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

  image.src = photo?.src || "";
  image.style.objectPosition = photo?.position || "";
  image.style.setProperty("--photo-filter", photo?.filter || "");

  if (caption) {
    caption.textContent = photo?.caption || "";
  }

  setPhotoTriggerAttributes(card, index, triggerLabel);
}

function renderExperience(content, theme) {
  const container = document.getElementById("experience-list");
  if (!container) {
    return;
  }

  container.innerHTML = "";

  content.items.forEach((item) => {
    const article = document.createElement("article");
    article.className = "timeline-item";

    const companyRow = document.createElement("div");
    companyRow.className = "timeline-company-row";

    const companyMain = document.createElement("div");
    companyMain.className = "timeline-company-main";

    let companyLink = null;
    if (item.companyUrl) {
      companyLink = document.createElement("a");
      companyLink.className = "company-link";
      companyLink.href = item.companyUrl;
      companyLink.target = "_blank";
      companyLink.rel = "noopener noreferrer";
      companyLink.textContent = item.company;
    } else {
      companyLink = document.createElement("span");
      companyLink.className = "company-link";
      companyLink.textContent = item.company;
    }

    const companyIconPath = theme === "dark" && item.companyIconDark ? item.companyIconDark : item.companyIcon;
    if (companyIconPath) {
      const companyIcon = document.createElement("img");
      companyIcon.className = "company-icon";
      companyIcon.alt = `${item.company} icon`;
      companyIcon.src = companyIconPath;
      companyMain.append(companyIcon);
    }
    companyMain.append(companyLink);
    companyRow.append(companyMain);

    if (item.companyUrl) {
      const companySiteLink = document.createElement("a");
      companySiteLink.className = "company-site-link";
      companySiteLink.href = item.companyUrl;
      companySiteLink.target = "_blank";
      companySiteLink.rel = "noopener noreferrer";

      const extIcon = document.createElement("img");
      extIcon.src = getThemedIcon("external-link", theme);
      extIcon.alt = "";
      extIcon.setAttribute("aria-hidden", "true");

      const extLabel = document.createElement("span");
      extLabel.textContent = content.companySiteLabel;

      companySiteLink.append(extIcon, extLabel);
      companyRow.append(companySiteLink);
    }

    const role = document.createElement("h3");
    role.className = "timeline-role";
    role.textContent = item.role;

    const meta = document.createElement("p");
    meta.className = "timeline-meta";
    meta.textContent = `${item.period} · ${item.location}`;

    const intro = document.createElement("p");
    intro.className = "timeline-intro";
    intro.textContent = item.intro;

    const list = document.createElement("ul");
    list.className = "timeline-list";
    item.bullets.forEach((bullet) => {
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

function renderEducation(content, theme) {
  const container = document.getElementById("education-list");
  if (!container) {
    return;
  }

  container.innerHTML = "";
  content.items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "education-item";

    const institutionRow = document.createElement("div");
    institutionRow.className = "timeline-company-row";

    const institution = document.createElement("h3");
    institution.className = "education-heading timeline-company-main";

    const institutionIconPath =
      theme === "dark" && item.institutionIconDark ? item.institutionIconDark : item.institutionIcon;
    if (institutionIconPath) {
      const institutionIcon = document.createElement("img");
      institutionIcon.className = "company-icon";
      institutionIcon.alt = `${item.institution} icon`;
      institutionIcon.src = institutionIconPath;
      institution.append(institutionIcon);
    }

    let institutionLink = null;
    if (item.institutionUrl) {
      institutionLink = document.createElement("a");
      institutionLink.className = "company-link";
      institutionLink.href = item.institutionUrl;
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

    if (item.institutionUrl) {
      const institutionSiteLink = document.createElement("a");
      institutionSiteLink.className = "company-site-link";
      institutionSiteLink.href = item.institutionUrl;
      institutionSiteLink.target = "_blank";
      institutionSiteLink.rel = "noopener noreferrer";

      const extIcon = document.createElement("img");
      extIcon.src = getThemedIcon("external-link", theme);
      extIcon.alt = "";
      extIcon.setAttribute("aria-hidden", "true");

      const extLabel = document.createElement("span");
      extLabel.textContent = content.institutionSiteLabel || "University site";

      institutionSiteLink.append(extIcon, extLabel);
      institutionRow.append(institutionSiteLink);
    }

    const degree = document.createElement("p");
    degree.className = "education-degree";
    degree.textContent = item.degree;

    const meta = document.createElement("p");
    meta.className = "education-meta";
    meta.textContent = item.period;

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
    image.src = item.src;
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

function renderPhotoLightbox() {
  const item = photoLightboxState.items[photoLightboxState.index];
  const image = document.getElementById("lightbox-image");
  const caption = document.getElementById("lightbox-caption");
  const counter = document.getElementById("lightbox-counter");
  const prevButton = document.getElementById("lightbox-prev");
  const nextButton = document.getElementById("lightbox-next");

  if (!item || !image) {
    return;
  }

  image.src = item.src;
  image.style.setProperty("--photo-filter", item.filter || "");
  if (caption) {
    caption.textContent = item.caption || "";
  }
  if (counter) {
    counter.textContent = `${photoLightboxState.index + 1} / ${photoLightboxState.items.length}`;
  }
  if (prevButton) {
    prevButton.disabled = photoLightboxState.index === 0;
  }
  if (nextButton) {
    nextButton.disabled = photoLightboxState.index >= photoLightboxState.items.length - 1;
  }
}

function syncPhotoLightboxItems(items) {
  photoLightboxState.items = items;
  if (photoLightboxState.index >= items.length) {
    photoLightboxState.index = Math.max(0, items.length - 1);
  }

  const lightbox = document.getElementById("photo-lightbox");
  if (lightbox && !lightbox.hidden) {
    renderPhotoLightbox();
  }
}

function openPhotoLightbox(index, opener) {
  const lightbox = document.getElementById("photo-lightbox");
  const dialog = document.getElementById("lightbox-dialog");
  if (!lightbox || !dialog || !photoLightboxState.items.length) {
    return;
  }

  photoLightboxState.index = Math.max(0, Math.min(index, photoLightboxState.items.length - 1));
  photoLightboxState.opener = opener || null;
  lightbox.hidden = false;
  document.body.classList.add("lightbox-open");
  renderPhotoLightbox();
  window.requestAnimationFrame(() => dialog.focus());
}

function closePhotoLightbox() {
  const lightbox = document.getElementById("photo-lightbox");
  if (!lightbox || lightbox.hidden) {
    return;
  }

  lightbox.hidden = true;
  document.body.classList.remove("lightbox-open");
  if (photoLightboxState.opener instanceof HTMLElement) {
    photoLightboxState.opener.focus();
  }
}

function showAdjacentPhoto(direction) {
  const nextIndex = photoLightboxState.index + direction;
  if (nextIndex < 0 || nextIndex >= photoLightboxState.items.length) {
    return;
  }

  photoLightboxState.index = nextIndex;
  renderPhotoLightbox();
}

function syncResumeLinks(resume) {
  const configs = [
    { key: "pdf", id: "resume-pdf", labelId: "resume-pdf-label" },
    { key: "docx", id: "resume-docx", labelId: "resume-docx-label" },
    { key: "txt", id: "resume-txt", labelId: "resume-txt-label" },
  ];

  configs.forEach((item) => {
    const link = document.getElementById(item.id);
    const label = document.getElementById(item.labelId);
    if (!link || !label) {
      return;
    }

    link.href = resume.files[item.key];
    link.setAttribute("download", resume.downloadNames[item.key]);
    label.textContent = resume.labels[item.key];
  });
}

function updateThemeSwitcher(lang, theme) {
  const data = CONTENT[lang] || CONTENT.en;
  const switcher = document.getElementById("theme-switch");

  if (switcher) {
    switcher.setAttribute("aria-label", data.theme.switcherLabel);
    document.querySelectorAll("[data-theme-switch]").forEach((button) => {
      const buttonTheme = button.getAttribute("data-theme-switch");
      const label = buttonTheme === "dark" ? data.theme.toDark : data.theme.toLight;
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

function renderLanguage(lang) {
  const data = CONTENT[lang] || CONTENT.en;
  const theme = document.documentElement.getAttribute("data-theme") || "light";
  const photoItems = getPhotoItems(data);

  document.documentElement.lang = lang;
  document.title = data.meta.title;

  const description = document.querySelector('meta[name="description"]');
  if (description) {
    description.setAttribute("content", data.meta.description);
  }

  setText("brand-link", data.brand);
  setText("nav-resume", data.nav.resume);
  setText("nav-experience", data.nav.experience);
  setText("nav-education", data.nav.education);
  setText("nav-strengths", data.nav.strengths);
  setText("nav-skills", data.nav.skills);

  const langSwitch = document.getElementById("lang-switch");
  if (langSwitch) {
    langSwitch.setAttribute("aria-label", data.langSwitcherLabel);
  }

  setText("hero-kicker", data.hero.kicker);
  setText("hero-name", data.hero.name);
  setText("hero-role", data.hero.role);
  setText("hero-summary", data.hero.summary);

  HERO_CONTACT_BINDINGS.forEach((item) => {
    const label = getContactLabel(item.key);
    const link = document.getElementById(item.id);
    setLinkLabel(item.id, label);
    setLinkHref(item.id, getContactHref(item.key));
    setImageSource(`#${item.id} img`, getContactIcon(item.key, theme));
    if (link) {
      link.title = String(RESUME_DATA.contacts?.[item.key]?.value || label);
    }
  });

  renderFacts(data.hero.facts, theme);
  renderHeroPhoto(data.hero.photo, 0, `${data.lightbox.openPhoto}: ${data.hero.photo.caption || ""}`);

  setText("resume-title", data.resume.title);
  syncResumeLinks(data.resume);

  setText("experience-title", data.experience.title);
  renderExperience(data.experience, theme);

  setText("education-title", data.education.title);
  setText("education-subtitle", data.education.subtitle);
  renderEducation(data.education, theme);

  setText("strengths-title", data.strengths.title);
  setText("strengths-subtitle", data.strengths.subtitle);
  renderStrengths(data.strengths.cards);

  setText("skills-title", data.skills.title);
  renderSkills(data.skills.groups);

  setText("preferences-title", data.preferences.title);
  renderPreferences(data.preferences.items);

  setText("photos-title", data.gallery.title);
  setText("photos-subtitle", data.gallery.subtitle);
  renderGallery(data.gallery.items, 1, data.lightbox.openPhoto);
  updatePhotoLightboxLabels(data.lightbox);
  syncPhotoLightboxItems(photoItems);

  setText("footer-text", data.footer.replace("{year}", String(new Date().getFullYear())));

  setLanguageButtonsState(lang);
  updateThemeSwitcher(lang, theme);
  updateHeaderOffset();
}

function applyLanguage(lang) {
  const safeLang = SUPPORTED_LANGS.includes(lang) ? lang : "en";

  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, safeLang);
  } catch (_error) {
    // Ignore storage access restrictions.
  }

  updateLanguageQuery(safeLang);
  renderLanguage(safeLang);
}

function setupLanguageSwitcher() {
  document.querySelectorAll("[data-lang-switch]").forEach((button) => {
    button.addEventListener("click", () => {
      const lang = button.getAttribute("data-lang-switch") || "en";
      applyLanguage(lang);
    });
  });
}

function setupThemeSwitcher() {
  document.querySelectorAll("[data-theme-switch]").forEach((button) => {
    button.addEventListener("click", () => {
      const nextTheme = button.getAttribute("data-theme-switch") || "light";
      setTheme(nextTheme);

      const currentLang = document.documentElement.lang || detectLanguage();
      renderLanguage(currentLang);
      updateHeaderOffset();
    });
  });
}

function setupPhotoLightbox() {
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }

    const trigger = event.target.closest("[data-photo-index]");
    if (trigger && !trigger.closest("#photo-lightbox")) {
      openPhotoLightbox(Number(trigger.dataset.photoIndex || "0"), trigger);
      return;
    }

    if (event.target.closest("[data-lightbox-close]") || event.target.closest("#lightbox-close")) {
      closePhotoLightbox();
      return;
    }

    if (event.target.closest("#lightbox-prev")) {
      showAdjacentPhoto(-1);
      return;
    }

    if (event.target.closest("#lightbox-next")) {
      showAdjacentPhoto(1);
    }
  });

  document.addEventListener("keydown", (event) => {
    const lightbox = document.getElementById("photo-lightbox");

    if (event.target instanceof Element) {
      const trigger = event.target.closest("[data-photo-index]");
      if (trigger && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        openPhotoLightbox(Number(trigger.dataset.photoIndex || "0"), trigger);
        return;
      }
    }

    if (!lightbox || lightbox.hidden) {
      return;
    }

    if (event.key === "Escape") {
      closePhotoLightbox();
      return;
    }

    if (event.key === "ArrowLeft") {
      showAdjacentPhoto(-1);
      return;
    }

    if (event.key === "ArrowRight") {
      showAdjacentPhoto(1);
    }
  });
}

function init() {
  const lang = detectLanguage();
  const theme = detectTheme();

  setTheme(theme);
  updateHeaderOffset();
  setupLanguageSwitcher();
  setupThemeSwitcher();
  setupPhotoLightbox();
  window.addEventListener("resize", updateHeaderOffset);
  window.addEventListener("load", updateHeaderOffset);
  applyLanguage(lang);
}

init();
