# Web Creator Bot v2 — Fixed

Versi ini memperbaiki flow v2 sebelumnya:
- FSM aiogram 3 menggunakan `StateFilter` secara eksplisit.
- Field template diproses berurutan, termasuk text/textarea/photos/audio.
- `/skip` hanya melewati field yang tidak wajib.
- `/selesai` hanya menyelesaikan field photos, lalu lanjut ke field berikutnya.
- `{{gallery}}` dan `{{music}}` diubah menjadi path media yang benar.
- Deploy Netlify memakai ZIP dari seluruh `published_site`, sehingga deploy baru tidak menghapus website lama.
- Semua website di SQLite dibangun ulang setiap deploy.
- Ada `/admin` + broadcast + statistik + template list + redeploy.
- Gunakan Railway Volume untuk `data/` dan `uploads/` jika ingin data/media tetap ada setelah restart/redeploy container.

## Environment
Lihat `.env.example`.

## Jalankan
`pip install -r requirements.txt`
`python server.py`

## Netlify
Buat satu Netlify Site kosong, ambil Site ID, buat Personal Access Token, lalu isi env.
Netlify Site tidak dibuat satu per user. Semua website berada di:
`https://domain-kamu/w/<ID>/`
