# IF Dashboard

Dashboard web real-time untuk mengontrol dan memantau pesawat di **Infinite Flight**, lewat **IF Connect API**. Jalan di jaringan lokal (WiFi) — laptop/PC kamu jadi "jembatan" antara browser dan HP yang menjalankan Infinite Flight.

![status](https://img.shields.io/badge/status-active-brightgreen) ![python](https://img.shields.io/badge/python-3.8%2B-blue)

---

## Fitur

- **Instrumen real-time** — Altitude, Airspeed, Heading, Vertical Speed, Ground Speed, Mach
- **Autopilot** — ALT / V/S / SPD (otomatis switch tampilan kts↔Mach) / HDG, semua AP hold mode, LNAV/VNAV/APPR
- **Flight Controls** — Gear, Spoilers, Flaps, Park Brake, Auto Brake, V.Trim (input angka langsung)
- **Systems & Power** — Battery, External Power, APU, Ground Power, Engine 1/2 start-stop independen, AutoStart
- **Lights & Pax Signs** — Beacon, Nav, Strobes, Landing, Seat Belt, No Smoking
- **Load & Fuel** — Set PAX (estimasi dari berat), Cargo, dan Fuel langsung dari dashboard
- **Map** — Posisi pesawat real-time, trail penerbangan, **flight plan route** tergambar otomatis dari data game
- **PFD/ND** — Primary Flight Display & Navigation Display bergaya kokpit asli, plus versi mini floating
- **TOD/TOC Calculator** — Kalkulasi top of descent/climb offline, tidak menyentuh data game
- **Automation** — Rule "kalau jarak ke tujuan ≤ X NM, set ALT/V/S/SPD otomatis"
- **Network Discovery** — Tombol "Cari IF" bawaan, tidak perlu cari IP manual
- **Status bar live** — Lokasi (ground/on flight), Departure, Arrival, Flight Time — semua dari flight plan game

---

## Cara Kerja (Singkat)

```
┌─────────────┐        WiFi (LAN)        ┌──────────────┐        WebSocket        ┌───────────┐
│  Infinite   │◄────── TCP :10112 ──────►│   server.py  │◄──────── :5000 ────────►│  Browser  │
│  Flight     │                          │  (di laptop) │                          │ (dashboard)│
│  (HP)       │                          └──────────────┘                          └───────────┘
└─────────────┘
```

`server.py` **wajib** berada di jaringan WiFi yang **sama** dengan HP yang menjalankan Infinite Flight — ini batasan dari IF Connect API sendiri (murni LAN, tidak bisa lewat internet).

---

## Prasyarat

| Kebutuhan | Keterangan |
|---|---|
| Python 3.8+ | [python.org](https://www.python.org/downloads/) — saat install, centang "Add Python to PATH" |
| Infinite Flight | Versi apapun yang mendukung IF Connect API v2 |
| **Infinite Flight Pro/Live** | IF Connect biasanya butuh subscription aktif |
| Laptop/PC & HP di WiFi yang sama | Wajib, tidak bisa beda jaringan |

---

## Instalasi

### 1. Clone / download repo

```bash
git clone https://github.com/<username>/if-dashboard.git
cd if-dashboard
```

### 2. Install dependency Python

```bash
pip install flask flask-socketio
```

### 3. Jalankan server

```bash
python server.py
```

Kalau berhasil, akan muncul:
```
 * Running on http://0.0.0.0:5000
```

### 4. Buka dashboard

Buka browser (Chrome/Edge/Firefox), akses:

```
http://localhost:5000
```

> Selalu akses lewat `localhost:5000` — **jangan** buka file `dashboard.html` langsung (double-click). File yang dibuka manual tidak bisa konek ke server sama sekali karena WebSocket butuh koneksi HTTP asli.

---

## Menghubungkan ke Infinite Flight

1. Di HP, buka **Infinite Flight** → mulai penerbangan (bisa di darat/gate)
2. Masuk **Settings → General → IF Connect** → aktifkan
3. Pastikan HP dan laptop **di WiFi yang sama**
4. Di dashboard, klik **🔍 Cari IF** — akan otomatis mendeteksi IP HP di jaringan
5. Muncul notifikasi "IF Ditemukan" → klik **Connect Sekarang**

Kalau discovery gagal mendeteksi, isi IP HP manual (lihat di HP: **Settings → Wi-Fi → tap nama jaringan → lihat IP Address**), lalu klik **Connect**.

---

## Struktur File

```
if-dashboard/
├── server.py         # Backend — Flask + SocketIO, jembatan ke IF Connect API
├── dashboard.html     # Frontend — dibuka lewat browser via server.py
└── README.md
```

---

## Troubleshooting

| Masalah | Kemungkinan Penyebab & Solusi |
|---|---|
| "Cari IF" tidak menemukan apa-apa | IF Connect belum diaktifkan di HP; HP & laptop beda WiFi; firewall Windows memblokir Python — izinkan saat muncul prompt |
| Connect berhasil tapi command tidak ngefek | Coba diskonek-konek ulang; pastikan Infinite Flight tetap di foreground (tidak di-lock/background) |
| Trim tidak berubah di pesawat | Trim dikirim langsung via SetState — tidak butuh setup binding apapun, coba cek versi `server.py` terbaru |
| Port 5000 sudah dipakai | Aplikasi lain memakai port itu — matikan aplikasi tersebut, atau ubah port di baris terakhir `server.py` |

---

## Batasan

- IF Connect API **murni jaringan lokal** — dashboard **tidak bisa** diakses dari luar WiFi rumah tanpa tunneling tambahan (ngrok, Cloudflare Tunnel, dll — di luar cakupan proyek ini)
- Beberapa kontrol (misal Ground Power) bergantung pada apakah IF mengekspos path yang bersangkutan sebagai *read-write* — sebagian aircraft/versi IF mungkin membatasi ini
- Data PAX (jumlah orang) adalah **estimasi** dari total berat — API IF tidak mengekspos jumlah penumpang asli, hanya total massa payload

---

## Lisensi

Proyek pribadi/hobi. Gunakan dan modifikasi bebas.
