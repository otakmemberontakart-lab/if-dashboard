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

Kontrol gamepad/keyboard fisik (opsional, terpisah dari dashboard) tersedia lewat script `joystick.py` dan `keyboard_control.py` — lihat bagian [Kontrol Fisik](#kontrol-fisik-opsional) di bawah.

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
├── server.py              # Backend — Flask + SocketIO, jembatan ke IF Connect API
├── dashboard.html          # Frontend — dibuka lewat browser via server.py
├── joystick.py             # (Opsional) kontrol gamepad fisik, dijalankan terpisah
├── keyboard_control.py     # (Opsional) kontrol keyboard, dijalankan terpisah
├── test_discovery.py       # (Opsional) tool diagnosa jaringan berdiri sendiri
└── README.md
```

`server.py` dan `dashboard.html` adalah **inti** — cukup dua file ini untuk dashboard jalan penuh. File lainnya bersifat tambahan/opsional.

---

## Kontrol Fisik (Opsional)

Dashboard adalah kontrol via klik/ketik di browser. Kalau mau kontrol lebih presisi pakai gamepad atau keyboard, ada dua script **terpisah** — dijalankan bersamaan dengan `server.py` di terminal yang berbeda (bukan lewat dashboard).

### Gamepad (`joystick.py`)

```bash
pip install pygame-ce
python joystick.py
```

Tanpa argumen → otomatis mencari IF di jaringan (sama seperti tombol "Cari IF" di dashboard). Kalau mau connect manual: `python joystick.py <IP_HP>`.

Setelah connect, **wajib** setup sekali di HP: **Settings → Controls → Virtual Joystick** → bind Axis 0-3 ke Roll/Pitch/Yaw/Throttle (persis seperti setting joystick fisik biasa).

### Keyboard (`keyboard_control.py`)

```bash
pip install keyboard
```

> **Windows: wajib jalankan PowerShell sebagai Administrator**, kalau tidak, tombol tidak akan terdeteksi sama sekali.

```bash
python keyboard_control.py <IP_HP>
```

| Kontrol | Tombol |
|---|---|
| Pitch naik/turun | `W`/`↑` naik, `S`/`↓` turun |
| Roll kiri/kanan | `A`/`←` kiri, `D`/`→` kanan |
| Yaw kiri/kanan | `Left Ctrl` kiri, `Right Ctrl` kanan |
| Throttle naik/turun | `Right Shift` naik, `Left Shift` turun |
| Reverse thrust | `Space` (tahan) |
| Flaps | `0`–`4` |
| Gear / Park Brake / Spoilers | `G` / `P` / `C` |
| Nav / Beacon lights | `N` / `B` |
| AP toggle / AutoStart | `Tab` / `Enter` |
| Engine 1 / 2 toggle | `I` / `O` |
| Debug mode / Keluar | `F1` / `Esc` |

Kedua script menggunakan mekanisme **virtual joystick** IF Connect API — bukan menekan tombol di layar HP secara langsung, jadi tetap responsif dan tidak mengganggu tampilan game.

---

## Troubleshooting

| Masalah | Kemungkinan Penyebab & Solusi |
|---|---|
| "Cari IF" tidak menemukan apa-apa | IF Connect belum diaktifkan di HP; HP & laptop beda WiFi; firewall Windows memblokir Python — izinkan saat muncul prompt |
| Connect berhasil tapi command tidak ngefek | Coba diskonek-konek ulang; pastikan Infinite Flight tetap di foreground (tidak di-lock/background) |
| Trim tidak berubah di pesawat | Trim dikirim langsung via SetState — tidak butuh setup binding apapun, coba cek versi `server.py` terbaru |
| Roll/Pitch/Yaw/Throttle di `joystick.py` tidak ngefek | Belum di-bind di HP: **Settings → Controls → Virtual Joystick** |
| Keyboard tidak terdeteksi (`keyboard_control.py`) | PowerShell harus dijalankan **as Administrator** |
| Port 5000 sudah dipakai | Aplikasi lain memakai port itu — matikan aplikasi tersebut, atau ubah port di baris terakhir `server.py` |

---

## Batasan

- IF Connect API **murni jaringan lokal** — dashboard **tidak bisa** diakses dari luar WiFi rumah tanpa tunneling tambahan (ngrok, Cloudflare Tunnel, dll — di luar cakupan proyek ini)
- Beberapa kontrol (misal Ground Power) bergantung pada apakah IF mengekspos path yang bersangkutan sebagai *read-write* — sebagian aircraft/versi IF mungkin membatasi ini
- Data PAX (jumlah orang) adalah **estimasi** dari total berat — API IF tidak mengekspos jumlah penumpang asli, hanya total massa payload

---

## Lisensi

Proyek pribadi/hobi. Gunakan dan modifikasi bebas.
