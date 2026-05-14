- Resume content in English and Russian must always be fully identical in meaning and scope for all extensions.
- Resume experience must be sorted by work start date.
- In downloaded resumes, keep all current contacts and also include the configured website link from `resume/resume.yaml`.
- DOCX and PDF must look the same

## Repository structure

- `index.html` is a lightweight language-aware redirect to the generated localized resume pages.
- `en/index.html` and `ru/index.html` are generated static resume pages. They load styling from `assets/styles.css` and runtime behavior from `assets/app.js`; `assets/app.js` hydrates from JSON embedded in each generated page.
- `assets/` contains all website assets:
  - `assets/app.js` hydrates generated pages, handles theme switching, language switching, and page interactions.
  - `assets/styles.css` contains the website layout, responsive styles, themes, and visual states.
  - `assets/media/` contains resume media. Common images live at the folder root; light/dark theme-specific variants live in `assets/media/light/` and `assets/media/dark/`; ignored AVIF/WebP responsive variants are generated into `assets/media/generated/`.
  - `assets/og-cover-recruiter.*` contains Open Graph preview assets.
- `resume/` contains resume source data:
  - `resume/resume.yaml` is the canonical source for all resume content, website copy, contacts, localized labels, and downloadable resume text.
  - `resume/resume.schema.yaml` documents and validates the expected YAML structure.
- `en/` and `ru/` contain generated localized pages and localized PDF, DOCX, and TXT resume downloads.
- `scripts/` contains resume generation tooling:
  - `scripts/build_resume_formats.sh` is the main local/CI entry point. It selects a Python runtime with the required dependencies and calls the generator.
  - `scripts/generate_resume_outputs.py` reads `resume/resume.yaml` and generates static pages, responsive photo variants, and PDF, DOCX, and TXT resume files.
  - `scripts/templates/` contains Jinja2 HTML templates used by the generator.
- `.github/workflows/build-resumes.yml` builds the generated resume files and deploys the static site to GitHub Pages on changes to the site, resume data, scripts, or workflow.
- `.build/` is generated build directory and should not be treated as source of truth.
