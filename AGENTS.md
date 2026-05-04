- Resume content in English and Russian must always be fully identical in meaning and scope for all extensions.
- Resume experience must be sorted by work start date.
- In downloaded resumes, keep all current contacts and also include the website link in contacts: https://jorqen.github.io
- DOCX and PDF must look the same

## Repository structure

- `index.html` is the static single-page resume website. It loads styling from `assets/styles.css` and runtime behavior from `assets/app.js`; `assets/app.js` fetches and renders `resume/resume.yaml` in the browser.
- `assets/` contains all website assets:
  - `assets/app.js` loads `resume/resume.yaml`, renders localized resume sections, theme switching, language switching, and page interactions.
  - `assets/styles.css` contains the website layout, responsive styles, themes, and visual states.
  - `assets/media/` contains resume media. Common images live at the folder root; light/dark theme-specific variants live in `assets/media/light/` and `assets/media/dark/`.
  - `assets/og-cover-recruiter.*` contains Open Graph preview assets.
- `resume/` contains resume source data and downloadable outputs:
  - `resume/resume.yaml` is the canonical source for all resume content, website copy, contacts, localized labels, and downloadable resume text.
  - `resume/resume.schema.yaml` documents and validates the expected YAML structure.
  - `resume/en/` contains generated English resume downloads.
  - `resume/ru/` contains generated Russian resume downloads.
- `scripts/` contains resume generation tooling:
  - `scripts/build_resume_formats.sh` is the main local/CI entry point. It selects a Python runtime with the required dependencies and calls the generator.
  - `scripts/generate_resume_outputs.py` reads `resume/resume.yaml` and generates PDF, DOCX, and TXT resume files.
- `.github/workflows/build-resumes.yml` builds the generated resume files and deploys the static site to GitHub Pages on changes to the site, resume data, scripts, or workflow.
- `.build/` and `dist/` are generated build directories and should not be treated as source of truth.
