# Final core fix

- `manifest.json` is the single source of truth for wizard fields.
- Manifest is re-read when a template is selected and again at build time.
- Text answers are stored directly under the manifest field ID.
- Removed the duplicated text-handler path that could advance the wizard twice.
- Added `text_list` support with min/max.
- Photos/audio are stored under their manifest IDs (`gallery`, `music`, etc.).
- Builder replaces placeholders by manifest field ID and validates unknown placeholders.
- Uploaded media is copied into each generated site's `media/` directory.
- Template installation validates `index.html` placeholders against `manifest.json`.
- Build is staged atomically so a failed rebuild cannot erase the last known-good published tree.
- Netlify redirects are generated only for successfully built sites.
- The supplied 18 templates were checked: all manifests parse and all index.html placeholders match their manifest field IDs.
