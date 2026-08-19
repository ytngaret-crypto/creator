# Template Guide

Setiap template wajib punya `manifest.json`, `index.html`, dan boleh memiliki `style.css`, `script.js`, serta `assets/`.

Field:
- text
- textarea
- photos
- audio

Placeholder HTML harus sama dengan `field.id`, misalnya:
`{{judul}}`, `{{nama}}`, `{{pesan}}`, `{{gallery}}`, `{{music}}`.

Upload folder baru ke GitHub lalu redeploy bot. Setelah template tersedia, admin dapat mengeceknya dari `/admin`.
