- Resume content in English and Russian must always be fully identical in meaning and scope for all extensions.
- Resume experience must be sorted by work start date.
- In downloaded resumes, keep all current contacts and also include the website link in contacts: https://jorqen.github.io
- DOCX and PDF must look the same

## Repository structure

- `index.html` is the static single-page resume website. It loads generated resume data from `assets/generated/resume-content.js`, styling from `assets/styles.css`, and runtime behavior from `assets/app.js`.
- `assets/` contains all website assets:
  - `assets/app.js` renders localized resume content, theme switching, language switching, and page interactions.
  - `assets/styles.css` contains the website layout, responsive styles, themes, and visual states.
  - `assets/generated/` contains generated website data. Treat `assets/generated/resume-content.js` as build output derived from `resume/resume.yaml`.
  - `assets/icons/` contains shared file/contact/company icons plus light and dark theme icon variants.
  - `assets/photos/` contains profile photos used by the site.
  - `assets/og-cover-recruiter.*` contains Open Graph preview assets.
- `resume/` contains resume source data and downloadable outputs:
  - `resume/resume.yaml` is the canonical source for all resume content, website copy, contacts, localized labels, and downloadable resume text.
  - `resume/resume.schema.yaml` documents and validates the expected YAML structure.
  - `resume/en/` contains generated English resume downloads.
  - `resume/ru/` contains generated Russian resume downloads.
- `scripts/` contains resume generation tooling:
  - `scripts/build_resume_formats.sh` is the main local/CI entry point. It selects a Python runtime with the required dependencies and calls the generator.
  - `scripts/generate_resume_outputs.py` reads `resume/resume.yaml` and generates website data plus PDF, DOCX, and TXT resume files.
- `.github/workflows/build-resumes.yml` builds the generated resume files and deploys the static site to GitHub Pages on changes to the site, resume data, scripts, or workflow.
- `.build/` and `dist/` are generated build directories and should not be treated as source of truth.
