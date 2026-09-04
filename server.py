#!/usr/bin/env python3
"""
IF Dashboard Server v7
Unit fix: IF kirim data dalam aviation units (ft, kts, fpm) — TANPA konversi.
Satu-satunya pengecualian: heading dalam RADIANS → perlu × 57.2958.
"""
import socket, struct, json, threading, time, math
from flask import Flask, send_file, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'ifdashboard'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

DISCOVERY_PORT = 15000
FALLBACK_PORT  = 10112

st = {
    'connected': False, 'if_ip': None, 'if_port': None,
    'sock': None, 'lock': threading.Lock(), 'manifest': {},
}

DTYPE_BOOL=0; DTYPE_INT=1; DTYPE_FLOAT=2; DTYPE_DOUBLE=3; DTYPE_STRING=4; DTYPE_LONG=5

# Trim dashboard (slider) dikirim lewat virtual joystick axis, BUKAN SetState
# langsung ke elevator_trim (itu ternyata di-override tiap frame, sama seperti
# throttle dulu). Axis 4 dipakai khusus buat trim — perlu di-bind manual sekali
# di IF: Settings > Controls > Virtual Joystick > Axis 4 → Trim.
VJOY_TRIM_AXIS = 4

# ── Unit notes ────────────────────────────────────────────────────────────────
# IF Connect API sends data dalam AVIATION UNITS:
#   altitude   → feet (langsung pakai, NO conversion)
#   airspeed   → knots (NO conversion)
#   VS         → ft/min (NO conversion)
#   groundspeed→ knots (NO conversion)
#   mach       → dimensionless (NO conversion)
#   heading    → RADIANS (PERLU × 57.2958 → degrees)
#
# AP targets menggunakan unit yang sama:
#   ap.alt target → feet (NO conversion)
#   ap.spd target → knots (NO conversion)
#   ap.vs  target → ft/min (NO conversion)
#   ap.hdg target → RADIANS (perlu × 57.2958 saat display, perlu ÷ 57.2958 saat set)

def rad2deg(v): return round(math.degrees(float(v))) % 360
def deg2rad(v): return math.radians(float(v))

# ── Binary protocol ───────────────────────────────────────────────────────────
def pack_get(sid):      return struct.pack('<i?', sid, False)
def pack_run(cid):      return struct.pack('<i?', cid, False)
def pack_set_b(sid, v): return struct.pack('<i??', sid, True, bool(v))
def pack_set_i(sid, v): return struct.pack('<i?i', sid, True, int(v))
def pack_set_f(sid, v): return struct.pack('<i?f', sid, True, float(v))

def pack_set(sid, dtype, value):
    if dtype == DTYPE_BOOL:   return pack_set_b(sid, value)
    if dtype == DTYPE_INT:    return pack_set_i(sid, value)
    if dtype == DTYPE_FLOAT:  return pack_set_f(sid, value)
    if dtype == DTYPE_DOUBLE: return struct.pack('<i?d', sid, True, float(value))
    if dtype == DTYPE_LONG:   return struct.pack('<i?q', sid, True, int(value))
    raise ValueError(f'dtype {dtype}')

def recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        c = sock.recv(n - len(buf))
        if not c: raise ConnectionError('Disconnected')
        buf += c
    return buf

def read_response(sock):
    hdr = recv_exact(sock, 8)
    rid, length = struct.unpack('<ii', hdr)
    return rid, length, recv_exact(sock, length)

def decode_value(data, dtype):
    try:
        if dtype == DTYPE_BOOL:
            if len(data) < 1: return None
            return bool(struct.unpack('<?', data[:1])[0])
        if dtype == DTYPE_INT:
            if len(data) < 4: return None
            return struct.unpack('<i', data[:4])[0]
        if dtype == DTYPE_FLOAT:
            if len(data) < 4: return None
            v = struct.unpack('<f', data[:4])[0]
            import math
            return None if math.isnan(v) or math.isinf(v) else v
        if dtype == DTYPE_DOUBLE:
            if len(data) < 8: return None
            return struct.unpack('<d', data[:8])[0]
        if dtype == DTYPE_LONG:
            if len(data) < 8: return None
            return struct.unpack('<q', data[:8])[0]
        if dtype == DTYPE_STRING:
            if len(data) < 4: return None
            slen = struct.unpack('<i', data[:4])[0]
            if slen < 0 or slen > 4096: return None   # sanity check
            if len(data) < 4 + slen: return None
            return data[4:4+slen].decode('utf-8', errors='ignore')
    except Exception:
        pass
    return None

# ── Discovery ─────────────────────────────────────────────────────────────────
def discover_if(timeout=20):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)
    try:
        sock.bind(('', DISCOVERY_PORT))
        print(f'Listening UDP {DISCOVERY_PORT}...')
        data, addr = sock.recvfrom(4096)
        raw = data.decode('utf-8', errors='ignore').strip()
        try:
            info = json.loads(raw)
            addrs = info.get('Addresses', [])
            ipv4 = [a for a in addrs if ':' not in a and a != '127.0.0.1']
            ip = ipv4[0] if ipv4 else info.get('Address', addr[0])
            return ip, int(info.get('Port', FALLBACK_PORT))
        except: return addr[0], FALLBACK_PORT
    except socket.timeout: return None, None
    finally: sock.close()

# ── Manifest ──────────────────────────────────────────────────────────────────
def load_manifest(sock):
    print('Loading manifest...')
    sock.sendall(struct.pack('<i?', -1, False))
    recv_exact(sock, 8)
    str_len = struct.unpack('<i', recv_exact(sock, 4))[0]
    manifest_str = recv_exact(sock, str_len).decode('utf-8', errors='ignore')
    manifest = {}
    for line in manifest_str.split('\n'):
        parts = line.strip().split(',', 2)
        if len(parts) == 3:
            try: manifest[parts[2]] = {'id': int(parts[0]), 'type': int(parts[1])}
            except: pass
    print(f'Manifest: {len(manifest)} states')
    return manifest

# ── SET_STATE ─────────────────────────────────────────────────────────────────
SET_STATE = {
    # AP — semua dalam aviation units kecuali HDG (radians, dikonversi di on_command)
    'ap.master':  ('aircraft/0/systems/autopilot/on',          DTYPE_BOOL,  None),
    'ap.alt':     ('aircraft/0/systems/autopilot/alt/target',  DTYPE_FLOAT, None),
    'ap.vs':      ('aircraft/0/systems/autopilot/vs/target',   DTYPE_FLOAT, None),
    'ap.spd':     ('aircraft/0/systems/autopilot/spd/target',  DTYPE_FLOAT, None),
    'ap.hdg':     ('aircraft/0/systems/autopilot/hdg/target',  DTYPE_FLOAT, None),
    'ap.altHold': ('aircraft/0/systems/autopilot/alt/on',      DTYPE_BOOL,  None),
    'ap.vsMode':  ('aircraft/0/systems/autopilot/vs/on',       DTYPE_BOOL,  None),
    'ap.spdHold': ('aircraft/0/systems/autopilot/spd/on',      DTYPE_BOOL,  None),
    'ap.hdgHold': ('aircraft/0/systems/autopilot/hdg/on',      DTYPE_BOOL,  None),
    'ap.appr':    ('aircraft/0/systems/autopilot/approach/on', DTYPE_BOOL,  None),
    'ap.lnav':    ('aircraft/0/systems/autopilot/nav/on',      DTYPE_BOOL,  None),
    # CATATAN: 'trim' TIDAK di sini — trim/elevator_trim ternyata sama seperti
    # throttle dulu: SetState langsung di-override tiap frame oleh kontrol asli.
    # Trim di-handle khusus via virtual joystick axis (lihat on_command + VJOY_TRIM_AXIS).
    # Lights
    'light.beacon':  ('aircraft/0/systems/beacon_lights_switch',  DTYPE_BOOL, None),
    'light.landing': ('aircraft/0/systems/landing_lights_switch', DTYPE_BOOL, None),
    'light.strobes': ('aircraft/0/systems/strobe_lights_switch',  DTYPE_BOOL, None),
    # Gear
    'gear.down': ('aircraft/0/systems/landing_gear/lever_state', DTYPE_BOOL, True),
    'gear.up':   ('aircraft/0/systems/landing_gear/lever_state', DTYPE_BOOL, False),
    # Flaps
    'flaps.0': ('aircraft/0/systems/flaps/state', DTYPE_INT, 0),
    'flaps.1': ('aircraft/0/systems/flaps/state', DTYPE_INT, 1),
    'flaps.2': ('aircraft/0/systems/flaps/state', DTYPE_INT, 2),
    'flaps.3': ('aircraft/0/systems/flaps/state', DTYPE_INT, 3),
    'flaps.4': ('aircraft/0/systems/flaps/state', DTYPE_INT, 4),
    # Spoilers (IF: 0=off, 1=FLIGHT, 2=ARMED)
    'spoilers.off':    ('aircraft/0/systems/spoilers/state', DTYPE_INT, 0),
    'spoilers.armed':  ('aircraft/0/systems/spoilers/state', DTYPE_INT, 2),
    'spoilers.flight': ('aircraft/0/systems/spoilers/state', DTYPE_INT, 1),
    # Auto Brake (path dikonfirmasi ada di manifest — value 0-3 asumsi umum,
    # mungkin perlu disesuaikan kalau ternyata levelnya beda di aircraft ini)
    'autobrake.off': ('aircraft/0/systems/auto_brakes/command_state', DTYPE_INT, 0),
    'autobrake.low': ('aircraft/0/systems/auto_brakes/command_state', DTYPE_INT, 1),
    'autobrake.med': ('aircraft/0/systems/auto_brakes/command_state', DTYPE_INT, 2),
    'autobrake.max': ('aircraft/0/systems/auto_brakes/command_state', DTYPE_INT, 3),
    # Signs
    'sign.seatbelt':  ('aircraft/0/systems/signs/seatbelt',   DTYPE_BOOL, None),
    'sign.nosmoking': ('aircraft/0/systems/signs/no_smoking', DTYPE_BOOL, None),
    # Electrical — direct state (dikirim dobel dengan ui_helper via on_command)
    'battery':   ('aircraft/0/systems/electrical_switch/master_switch/state', DTYPE_INT, None),
    'apu':       ('aircraft/0/systems/apu/apu/switch_state',                  DTYPE_INT, None),
    'ext.power': ('aircraft/0/systems/electrical_switch/ext_power_switch/state', DTYPE_INT, None),
    # CATATAN: aircraft/0/ground_services/gpu/state itu READ-ONLY di dokumentasi resmi!
    # gnd.power di-handle lewat RunCommand pencarian dinamis (lihat RUN_CMD_KEYWORDS), bukan SetState.
    # (Ground Services panel dihapus sementara — lihat riwayat kalau mau diaktifin lagi)
    # Engines — PER-MESIN, path spesifik (bukan generic Engine.Start/.Stop yang
    # gak bisa dibedain mesin mana). Start = simulasi tekan start_button
    # ui_helper mesin itu. Stop = paksa state langsung jadi 0 (off).
    # Engines: DI-HANDLE KHUSUS di on_command (bukan di sini) — butuh pola
    # tekan-lepas (press+release) buat start_button, dan dual-path buat stop.
    # Lihat blok 'engine1.on'/'engine2.on'/'engine1.off'/'engine2.off' di on_command.
}

# ── RunCommand: dicari DINAMIS dari manifest tiap connect (ID beda2 tiap sesi!) ──
# Pola sama persis kayak joystick.py — jangan hardcode ID, itu penyebab command
# "kelihatan gak jalan" padahal cuma ID-nya udah gak match sama manifest terkini.
RUN_CMD_KEYWORDS = {
    'brake':           ['parkingbrake', 'parkbrake'],
    'light.nav':       ['navlights'],
    'fuel.dump':       ['fueldump'],
    'autostart':       ['autostart'],
    'engine.stop.all': ['engine.stopall', 'enginestopall'],
    'gnd.power':       ['groundpower', 'gpu'],
    'trim.up':         ['elevatortrimup'],    # RunCommand, dijamin reliable (protokol sudah kebukti)
    'trim.down':       ['elevatortrimdown'],
    # CATATAN: engine1.on/off dan engine2.on/off SENGAJA TIDAK di sini —
    # commands/Engine.Start & Engine.Stop itu GENERIC, gak bisa target mesin
    # tertentu (itu penyebab Eng1/Eng2 button dulu gak bisa dibedain). Sekarang
    # per-mesin pakai path spesifik langsung (lihat SET_STATE di bawah).
}

# ── POLL_PATHS ────────────────────────────────────────────────────────────────
POLL_PATHS = [
    # Flight instruments (aviation units, NO conversion except heading)
    'aircraft/0/altitude_msl',
    'aircraft/0/indicated_airspeed',
    'aircraft/0/heading_magnetic',        # radians → convert to degrees
    'aircraft/0/vertical_speed',
    'aircraft/0/groundspeed',
    'aircraft/0/mach_speed',
    'aircraft/0/name',                    # nama pesawat yang sedang dipakai
    # Autopilot
    'aircraft/0/systems/autopilot/on',
    'aircraft/0/systems/autopilot/alt/target',
    'aircraft/0/systems/autopilot/alt/on',
    'aircraft/0/systems/autopilot/hdg/target',  # radians
    'aircraft/0/systems/autopilot/hdg/on',
    'aircraft/0/systems/autopilot/spd/mode',    # PENTING: poll SEBELUM spd/target! 0=IAS(kts) 1=Mach
    'aircraft/0/systems/autopilot/spd/target',
    'aircraft/0/systems/autopilot/spd/on',
    'aircraft/0/systems/autopilot/vs/target',
    'aircraft/0/systems/autopilot/vs/on',
    'aircraft/0/systems/autopilot/approach/on',
    'aircraft/0/systems/autopilot/nav/on',
    'aircraft/0/systems/axes/elevator_trim',    # trim posisi sekarang (raw = persen×10)
    # Flight controls
    'aircraft/0/systems/parking_brake/state',
    'aircraft/0/systems/landing_gear/state',
    'aircraft/0/systems/flaps/state',
    'aircraft/0/systems/spoilers/state',
    # Lights
    'aircraft/0/systems/beacon_lights_switch',
    'aircraft/0/systems/landing_lights_switch',
    'aircraft/0/systems/strobe_lights_switch',
    'aircraft/0/systems/nav_lights_switch',       # ini yang KELUPAAN sebelumnya
    'aircraft/0/systems/auto_brakes/command_state',
    # Engines + N1
    'aircraft/0/systems/engines/0/state',
    'aircraft/0/systems/engines/1/state',
    'aircraft/0/systems/engines/0/n1',     # N1 percentage
    'aircraft/0/systems/engines/1/n1',
    'aircraft/0/systems/engines/0/fuel_flow',
    'aircraft/0/systems/engines/1/fuel_flow',
    # Electrical
    'aircraft/0/systems/electrical_switch/master_switch/state',
    'aircraft/0/systems/electrical_switch/ext_power_switch/state',
    'aircraft/0/systems/apu/apu/state',
    'aircraft/0/ground_services/gpu/state',
    # Signs
    'aircraft/0/systems/signs/seatbelt',
    'aircraft/0/systems/signs/no_smoking',
    # Fuel
    'aircraft/0/systems/fuel/fuel_remaining',
    'aircraft/0/systems/fuel/tank/0/fuel_used',
    'aircraft/0/systems/fuel/tank/1/fuel_used',
    # Flight plan
    'aircraft/0/flightplan/destination_dist',
    'aircraft/0/flightplan/destination_ete',
    'aircraft/0/flightplan/next_waypoint_name',
    'aircraft/0/flightplan/next_waypoint_dist',
    # Battery
    'aircraft/0/systems/battery/main_battery/amp_hour',
    # ILS / NAV (NAV1 = primary ILS radio)
    'aircraft/0/systems/nav_sources/nav/1/has_localizer',
    'aircraft/0/systems/nav_sources/nav/1/distance_to_localizer',
    'aircraft/0/systems/nav_sources/nav/1/has_glideslope',
    'aircraft/0/systems/nav_sources/nav/1/glideslope_angle',
    # Position (for map)
    'aircraft/0/latitude',
    'aircraft/0/longitude',
    # Real aircraft attitude (radians → degrees, KEEP SIGN, no % 360)
    'aircraft/0/pitch',
    'aircraft/0/bank',
    # Status bar: lokasi + ground/flight
    'aircraft/0/is_on_ground',
    'infiniteflight/nearest_airport',
]

# ── SLOW POLL: data yang jarang berubah, di-poll tiap ~5 detik (bukan tiap 250ms) ──
SLOW_POLL_PATHS = [
    'aircraft/0/flightplan/coordinates',   # "lat,lng lat,lng ..." — semua waypoint
    'aircraft/0/flightplan/route',         # "WIMM,FI05,..." — nama waypoint, urutan match
    'aircraft/0/systems/load/items_count',
    'aircraft/0/systems/load/total_weight',
    'aircraft/0/systems/load/0/mass', 'aircraft/0/systems/load/0/name',
    'aircraft/0/systems/load/1/mass', 'aircraft/0/systems/load/1/name',
    'aircraft/0/systems/load/2/mass', 'aircraft/0/systems/load/2/name',
    'aircraft/0/systems/load/3/mass', 'aircraft/0/systems/load/3/name',
    'aircraft/0/systems/load/4/mass', 'aircraft/0/systems/load/4/name',
    # Status bar: departure, arrival, flight time
    'aircraft/0/last_takeoff_airport',
    'aircraft/0/flightplan/destination_waypoint_name',
    'aircraft/0/time_since_last_takeoff',
]
SLOW_POLL_EVERY = 8    # tiap 8 tick × 0.25s = ~2 detik (dipercepat dari 5 detik)

# ── process_telemetry ─────────────────────────────────────────────────────────
# Mode SPD Autopilot: 0 = IAS (knots), 1 = Mach — di-update live, dipakai
# proc() (buat display) dan on_command() (buat tau cara convert saat SET).
# Global sengaja dipakai di sini karena proc() dipanggil per-item tanpa akses ke `st`.
_ap_spd_mode = 0

def proc(path, v):
    global _ap_spd_mode
    if v is None: return None
    # ── Flight instruments ────────────────────────────────────────────────────
    # altitude: feet (IF position data sudah dalam aviation units)
    if   path == 'aircraft/0/altitude_msl':         return ('alt',   round(float(v)))
    # velocity: m/s → perlu konversi (IF physics engine pakai SI)
    elif path == 'aircraft/0/indicated_airspeed':   return ('spd',   round(float(v) * 1.94384))   # m/s → kt
    elif path == 'aircraft/0/heading_magnetic':     return ('hdg',   rad2deg(v))                   # rad → deg
    elif path == 'aircraft/0/vertical_speed':       return ('vs',    round(float(v) * 196.85))     # m/s → fpm
    elif path == 'aircraft/0/groundspeed':          return ('gs',    round(float(v) * 1.94384))    # m/s → kt
    elif path == 'aircraft/0/mach_speed':           return ('mach',  round(float(v), 3))
    elif path == 'aircraft/0/name':                 return ('ac_name', str(v).strip())
    # Autopilot
    # ── Current instrument reads (aviation units, NO conversion) ──
    elif path == 'aircraft/0/systems/autopilot/on':              return ('ap_active',  bool(v))
    # ── AP TARGETS are stored in SI units by IF (meters, m/s, m/min, radians) ──
    elif path == 'aircraft/0/systems/autopilot/alt/target':      return ('ap_alt',     round(float(v) * 3.28084))   # m → ft
    elif path == 'aircraft/0/systems/autopilot/alt/on':          return ('ap_alt_on',  bool(v))
    elif path == 'aircraft/0/systems/autopilot/hdg/target':      return ('ap_hdg',     rad2deg(v))                  # rad → deg
    elif path == 'aircraft/0/systems/autopilot/hdg/on':          return ('ap_hdg_on',  bool(v))
    elif path == 'aircraft/0/systems/autopilot/spd/mode':
        _ap_spd_mode = int(v)
        return ('ap_spd_mode', _ap_spd_mode)   # 0=IAS(kts) 1=Mach — HARUS dipoll SEBELUM spd/target
    elif path == 'aircraft/0/systems/autopilot/spd/target':
        if _ap_spd_mode == 1:
            return ('ap_spd', round(float(v), 3))                  # Mach — raw, TANPA konversi
        return ('ap_spd', round(float(v) * 1.94384))                # IAS  — m/s → kt
    elif path == 'aircraft/0/systems/autopilot/spd/on':          return ('ap_spd_on',  bool(v))
    elif path == 'aircraft/0/systems/autopilot/vs/target':       return ('ap_vs',      round(float(v) * 3.28084))   # m/min → ft/min
    elif path == 'aircraft/0/systems/autopilot/vs/on':           return ('ap_vs_on',   bool(v))
    elif path == 'aircraft/0/systems/autopilot/approach/on':     return ('ap_appr_on', bool(v))
    elif path == 'aircraft/0/systems/autopilot/nav/on':          return ('ap_lnav_on', bool(v))
    elif path == 'aircraft/0/systems/axes/elevator_trim':
        return ('trim', round(float(v) / 10, 1))   # raw IF = persen×10, bukan fraction -1..1
    # Flight controls
    elif path == 'aircraft/0/systems/parking_brake/state':       return ('park_brake', bool(v))
    elif path == 'aircraft/0/systems/landing_gear/state':        return ('gear_state', int(v))
    elif path == 'aircraft/0/systems/flaps/state':               return ('flaps_state',int(v))
    elif path == 'aircraft/0/systems/spoilers/state':            return ('sp_state',   int(v))
    # Lights
    elif path == 'aircraft/0/systems/beacon_lights_switch':      return ('lt_beacon',  bool(v))
    elif path == 'aircraft/0/systems/landing_lights_switch':     return ('lt_landing', bool(v))
    elif path == 'aircraft/0/systems/strobe_lights_switch':      return ('lt_strobes', bool(v))
    elif path == 'aircraft/0/systems/nav_lights_switch':         return ('lt_nav',     bool(v))
    elif path == 'aircraft/0/systems/auto_brakes/command_state': return ('autobrake_state', int(v))
    # Engines
    elif path == 'aircraft/0/systems/engines/0/state':           return ('eng1_state', int(v))
    elif path == 'aircraft/0/systems/engines/1/state':           return ('eng2_state', int(v))
    elif path == 'aircraft/0/systems/engines/0/n1':              return ('eng1_n1',    round(float(v) * 100, 1))
    elif path == 'aircraft/0/systems/engines/1/n1':              return ('eng2_n1',    round(float(v) * 100, 1))
    elif path == 'aircraft/0/systems/engines/0/fuel_flow':       return ('ff1',        round(float(v)))
    elif path == 'aircraft/0/systems/engines/1/fuel_flow':       return ('ff2',        round(float(v)))
    # Electrical
    elif path == 'aircraft/0/systems/electrical_switch/master_switch/state':
        return ('bat_state', int(v) > 0)
    elif path == 'aircraft/0/systems/electrical_switch/ext_power_switch/state':
        return ('ext_state', int(v) > 0)
    elif path == 'aircraft/0/systems/apu/apu/state':             return ('apu_state',  int(v))
    elif path == 'aircraft/0/ground_services/gpu/state':         return ('gpu_state',  bool(v))
    # Signs
    elif path == 'aircraft/0/systems/signs/seatbelt':            return ('sign_belt',  bool(v))
    elif path == 'aircraft/0/systems/signs/no_smoking':          return ('sign_smoke', bool(v))
    # Fuel
    elif path == 'aircraft/0/systems/fuel/fuel_remaining':       return ('fuel_rem',   round(float(v)))
    elif path == 'aircraft/0/systems/fuel/tank/0/fuel_used':     return ('fuel_used_0',round(float(v)))
    elif path == 'aircraft/0/systems/fuel/tank/1/fuel_used':     return ('fuel_used_1',round(float(v)))
    # Flight plan
    elif path == 'aircraft/0/flightplan/destination_dist':       return ('dest_dist',  round(float(v), 1))
    elif path == 'aircraft/0/flightplan/destination_ete':
        s = int(v); h, m = divmod(max(s,0)//60, 60)
        return ('dest_ete', f'{h}:{m:02d}' if s > 0 else '--:--')
    elif path == 'aircraft/0/flightplan/next_waypoint_name':     return ('next_wpt_name', str(v).strip() or '---')
    elif path == 'aircraft/0/flightplan/next_waypoint_dist':     return ('next_wpt_dist', round(float(v), 1))
    elif path == 'aircraft/0/systems/battery/main_battery/amp_hour':
        return ('bat_ah', round(float(v), 1))
    # ILS — NAV/1
    elif path == 'aircraft/0/systems/nav_sources/nav/1/has_localizer':       return ('loc_alive', bool(v))
    elif path == 'aircraft/0/systems/nav_sources/nav/1/distance_to_localizer': return ('loc_dev', round(float(v), 3))
    elif path == 'aircraft/0/systems/nav_sources/nav/1/has_glideslope':      return ('gs_alive',  bool(v))
    elif path == 'aircraft/0/systems/nav_sources/nav/1/glideslope_angle':    return ('gs_dev',    round(float(v), 3))
    # Position for map (already in degrees, no conversion needed)
    elif path == 'aircraft/0/latitude':  return ('lat', round(float(v), 6))
    elif path == 'aircraft/0/longitude': return ('lng', round(float(v), 6))
    # Aircraft attitude — radians → degrees, KEEP SIGN (no % 360)
    elif path == 'aircraft/0/pitch': return ('pitch', round(math.degrees(float(v)), 2))
    elif path == 'aircraft/0/bank':  return ('bank',  round(math.degrees(float(v)), 2))
    # Status bar
    elif path == 'aircraft/0/is_on_ground':        return ('on_ground', bool(v))
    elif path == 'infiniteflight/nearest_airport': return ('nearest_airport', str(v).strip())
    return None

def process_slow_poll(raw):
    """Parse hasil slow-poll (raw path→value dict) jadi field siap kirim ke dashboard.
    Dipanggil tiap ~5 detik — buat flightplan route (map) dan PAX/cargo load."""
    out = {}

    # ── Flight plan route: gabung coordinates + route names jadi waypoint list ──
    coords_str = (raw.get('aircraft/0/flightplan/coordinates') or '').strip()
    route_str  = (raw.get('aircraft/0/flightplan/route') or '').strip()
    if coords_str:
        try:
            pairs = coords_str.split(' ')
            names = [n.strip() for n in route_str.split(',')] if route_str else []
            waypoints = []
            for i, pair in enumerate(pairs):
                if ',' not in pair: continue
                lat_s, lng_s = pair.split(',', 1)
                waypoints.append({
                    'name': names[i] if i < len(names) else f'WPT{i+1}',
                    'lat': float(lat_s), 'lng': float(lng_s),
                })
            if waypoints: out['route_waypoints'] = waypoints
        except Exception as e:
            print(f'Parse flightplan coordinates error: {e}')

    # ── PAX / Cargo load — klasifikasi by nama item (varies per pesawat) ────────
    pax_weight, cargo_weight = 0, 0
    pax_idx, cargo_idx = [], []
    for i in range(5):
        mass = raw.get(f'aircraft/0/systems/load/{i}/mass')
        name = (raw.get(f'aircraft/0/systems/load/{i}/name') or '').lower()
        if mass is None: continue
        # Bulatkan tiap ITEM dulu (bukan jumlah dulu baru bulatkan) — lebih dekat
        # sama cara IF nampilin angka per-kompartemen.
        m = round(float(mass))
        if 'pax' in name or 'passenger' in name:
            pax_weight += m
            pax_idx.append(i)
        elif 'cargo' in name or 'freight' in name or 'baggage' in name or 'bag' in name:
            cargo_weight += m
            cargo_idx.append(i)
    # Simpan index-nya buat dipakai on_command waktu user SET dari dashboard
    st['pax_load_idx']   = pax_idx
    st['cargo_load_idx'] = cargo_idx

    if pax_weight > 0 or cargo_weight > 0:
        out['pax_weight_kg']   = pax_weight
        out['cargo_weight_kg'] = cargo_weight
        # Estimasi headcount — API IF gak expose jumlah PAX asli, cuma total berat.
        # 77kg = standar rata2 berat penumpang dewasa (tanpa bagasi terpisah,
        # karena baggage biasanya item terpisah). TETAP ESTIMASI, bukan angka pasti.
        out['pax_count_est'] = round(pax_weight / 77) if pax_weight > 0 else 0

    # ── Status bar: departure, arrival, flight time ──────────────────────────────
    dep = raw.get('aircraft/0/last_takeoff_airport')
    if dep is not None:
        out['departure'] = str(dep).strip()  # bisa string kosong kalau belum pernah takeoff

    arr = raw.get('aircraft/0/flightplan/destination_waypoint_name')
    if arr is not None:
        out['arrival'] = str(arr).strip()

    ft = raw.get('aircraft/0/time_since_last_takeoff')
    if ft is not None:
        secs = int(float(ft))
        if secs > 0:
            h, m = divmod(secs // 60, 60)
            out['flight_time'] = f'{h}:{m:02d}:{secs%60:02d}'
        else:
            out['flight_time'] = None

    return out

def _normalize(s):
    return s.lower().replace('.', '').replace('_', '').replace('-', '')

def find_run_cmd_id(manifest, keywords):
    """Cari RunCommand ID dari manifest berdasar keyword (path type == -1).
    Sama persis pola dengan joystick.py — ID RunCommand BISA BEDA tiap sesi/pesawat,
    jangan pernah di-hardcode."""
    for kw in keywords:
        kw_n = _normalize(kw)
        for path, info in manifest.items():
            if info.get('type') == -1 and kw_n in _normalize(path):
                return info['id'], path
    return None, None

def resolve_commands(manifest):
    """Dipanggil tiap kali fresh connect/reconnect — resolve semua SET_STATE
    dan RUN_CMD dari PATH STRING ke ID yang benar-benar valid di sesi SEKARANG.
    Ini fix utama supaya command tidak 'diam-diam gagal' karena ID basi."""
    resolved_set = {}
    for key, (path, dtype, fixed) in SET_STATE.items():
        info = manifest.get(path)
        if info:
            resolved_set[key] = (info['id'], dtype, fixed)
        else:
            print(f'  ⚠ SET_STATE "{key}": path "{path}" TIDAK ADA di manifest sesi ini')

    resolved_run = {}
    for key, keywords in RUN_CMD_KEYWORDS.items():
        cid, found_path = find_run_cmd_id(manifest, keywords)
        if cid is not None:
            resolved_run[key] = cid
        else:
            print(f'  ⚠ RUN_CMD "{key}": tidak ketemu command dengan keyword {keywords}')

    print(f'Resolved: {len(resolved_set)}/{len(SET_STATE)} SET_STATE, {len(resolved_run)}/{len(RUN_CMD_KEYWORDS)} RUN_CMD')
    return resolved_set, resolved_run

# ── Connect / Disconnect ──────────────────────────────────────────────────────
def connect_to_if(ip, port=None):
    with st['lock']:
        if st['sock']:
            try: st['sock'].close()
            except: pass
            st['sock'] = None
    port = port or FALLBACK_PORT
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5); sock.connect((ip, port)); sock.settimeout(3)
        with st['lock']:
            st['sock'] = sock; st['if_ip'] = ip; st['if_port'] = port; st['connected'] = True
        print(f'Connected: {ip}:{port}')
        manifest = load_manifest(sock)
        with st['lock']:
            st['manifest'] = manifest
            st['set_state'], st['run_cmd'] = resolve_commands(manifest)
        socketio.emit('status', {'connected': True, 'ip': ip, 'port': port})
        threading.Thread(target=poll_telemetry, daemon=True).start()
        return True
    except socket.timeout:
        socketio.emit('status', {'connected': False, 'error': f'Timeout {ip}:{port}'}); return False
    except ConnectionRefusedError:
        socketio.emit('status', {'connected': False, 'error': f'Refused {ip}:{port}'}); return False
    except Exception as e:
        socketio.emit('status', {'connected': False, 'error': str(e)}); return False

def disconnect_from_if():
    with st['lock']:
        st['connected'] = False
        if st['sock']:
            try: st['sock'].close()
            except: pass
            st['sock'] = None
    socketio.emit('status', {'connected': False})

# ── Telemetry polling ─────────────────────────────────────────────────────────
def poll_batch(ids_list):
    """Kirim SEMUA GetState request dulu (pipeline), baru baca semua respons.
    Jauh lebih cepat dari serial send→read→send→read per item: cuma butuh
    ~1 round-trip network buat SELURUH batch, bukan N round-trip.
    Kalau ada yang gagal di tengah, seluruh batch dianggap gagal — lebih aman
    daripada coba partial-recover yang berisiko bikin desync makin parah."""
    if not ids_list: return {}
    id_to_info = {sid: (dtype, path) for sid, dtype, path in ids_list}
    raw = {}
    with st['lock']:
        if not st['sock']: raise ConnectionError('no socket')
        for sid, dtype, path in ids_list:
            st['sock'].sendall(pack_get(sid))
        for _ in ids_list:
            resp_id, length, data = read_response(st['sock'])
            dtype2, path2 = id_to_info.get(resp_id, (None, None))
            if path2 is None: continue
            v = decode_value(data, dtype2)
            raw[path2] = v
    return raw

def poll_telemetry():
    manifest = st['manifest']
    poll_ids  = [(manifest[p]['id'], manifest[p]['type'], p) for p in POLL_PATHS if p in manifest]
    missing   = [p for p in POLL_PATHS if p not in manifest]
    print(f'Polling {len(poll_ids)} states (pipelined)' + (f', {len(missing)} missing' if missing else ''))
    for m in missing: print(f'  MISSING: {m}')

    slow_ids = [(manifest[p]['id'], manifest[p]['type'], p) for p in SLOW_POLL_PATHS if p in manifest]
    slow_missing = [p for p in SLOW_POLL_PATHS if p not in manifest]
    if slow_missing:
        print(f'  SLOW POLL missing {len(slow_missing)}: {slow_missing}')

    conn_err_count = 0
    tick = 0

    while st['connected']:
        try:
            raw = poll_batch(poll_ids)
            flight = {}
            for path, v in raw.items():
                r = proc(path, v)
                if r: flight[r[0]] = r[1]

            # ── Slow poll (tiap ~2 detik): flightplan coordinates/route, PAX/cargo ──
            tick += 1
            if slow_ids and tick % SLOW_POLL_EVERY == 0 and st['connected']:
                try:
                    slow_raw = poll_batch(slow_ids)
                    slow_flight = process_slow_poll(slow_raw)
                    if slow_flight: flight.update(slow_flight)
                except Exception as e:
                    print(f'Slow poll batch error: {e}')

            if flight:
                socketio.emit('telemetry', flight)
            conn_err_count = 0
            time.sleep(0.25)

        except socket.timeout:
            continue
        except Exception as e:
            if st['connected']:
                print(f'Poll cycle error: {e}')
            conn_err_count += 1
            if conn_err_count >= 3:
                break
            time.sleep(1)

    # ── Connection lost → auto-reconnect ─────────────────────────────────────
    last_ip   = st['if_ip']
    last_port = st['if_port']
    with st['lock']:
        st['connected'] = False
        if st['sock']:
            try: st['sock'].close()
            except: pass
            st['sock'] = None

    if not last_ip:
        socketio.emit('status', {'connected': False, 'error': 'Disconnected'})
        return

    socketio.emit('status', {'connected': False, 'reconnecting': True,
                             'error': 'Koneksi terputus — mencoba reconnect...'})
    print(f'Auto-reconnect ke {last_ip}:{last_port}...')

    # Tunggu lebih lama di awal — IF perlu waktu untuk load flight plan
    for attempt in range(1, 8):
        wait = 6 if attempt == 1 else 5   # tunggu 6 detik pertama, lalu 5 detik
        print(f'Tunggu {wait}s sebelum attempt {attempt}/7...')
        time.sleep(wait)

        if st['connected']: return   # user sudah reconnect manual

        print(f'Reconnect attempt {attempt}/7 ke {last_ip}:{last_port}...')
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(6)
            sock.connect((last_ip, last_port))
            sock.settimeout(4)
            with st['lock']:
                st['sock'] = sock
                st['if_ip'] = last_ip
                st['if_port'] = last_port
                st['connected'] = True
            new_manifest = load_manifest(sock)
            with st['lock']:
                st['manifest'] = new_manifest
                st['set_state'], st['run_cmd'] = resolve_commands(new_manifest)
            print(f'Reconnected ✓ attempt {attempt}')
            socketio.emit('status', {'connected': True, 'ip': last_ip,
                                     'port': last_port, 'reconnected': True})
            threading.Thread(target=poll_telemetry, daemon=True).start()
            return
        except Exception as e:
            print(f'Attempt {attempt} gagal: {e}')
            socketio.emit('status', {'connected': False, 'reconnecting': True,
                                     'error': f'Reconnecting ({attempt}/7)...'})
            try: sock.close()
            except: pass

    socketio.emit('status', {'connected': False,
                             'error': 'Gagal reconnect. Tekan Connect.'})

# ── Send helpers ──────────────────────────────────────────────────────────────
def send_raw(data):
    try:
        with st['lock']:
            if st['sock']: st['sock'].sendall(data); return True
    except Exception as e: print(f'Send error: {e}')
    return False

# ── WebSocket ─────────────────────────────────────────────────────────────────
@socketio.on('connect')
def on_ws(): emit('status', {'connected': st['connected'], 'ip': st['if_ip']})

@socketio.on('connect_if')
def on_connect_if(data):
    ip = (data.get('ip') or '').strip()
    port = int(data.get('port') or FALLBACK_PORT)
    if ip:
        threading.Thread(target=connect_to_if, args=(ip, port), daemon=True).start()
    else:
        def auto():
            dip, dp = discover_if(20)
            if dip: connect_to_if(dip, dp)
            else: socketio.emit('status', {'connected': False, 'error': 'IF tidak terdeteksi'})
        threading.Thread(target=auto, daemon=True).start()

@socketio.on('disconnect_if')
def on_disconnect_if(): disconnect_from_if()

@socketio.on('discover_only')
def on_discover_only():
    """Cari IF di jaringan TANPA langsung connect — hasil dikirim ke dashboard
    biar user bisa konfirmasi dulu sebelum connect."""
    def run():
        socketio.emit('discover_result', {'searching': True})
        dip, dp = discover_if(15)
        if dip:
            socketio.emit('discover_result', {'found': True, 'ip': dip, 'port': dp})
        else:
            socketio.emit('discover_result', {'found': False,
                'error': 'IF tidak terdeteksi dalam 15 detik. Pastikan IF Connect aktif '
                         '(Settings > General) dan HP+laptop di WiFi yang sama.'})
    threading.Thread(target=run, daemon=True).start()

@socketio.on('command')
def on_command(data):
    if not st['connected']: return
    cmd   = data.get('type', '')
    value = data.get('value')
    print(f'CMD: {cmd} = {value}')

    set_state = st.get('set_state', {})
    run_cmd   = st.get('run_cmd', {})
    manifest  = st.get('manifest', {})

    # ── AP targets: convert aviation units → SI before sending to IF ──────────
    if value is not None:
        if   cmd == 'ap.alt': value = float(value) / 3.28084   # ft → m
        elif cmd == 'ap.vs':  value = float(value) / 3.28084   # ft/min → m/min
        elif cmd == 'ap.spd':
            # Mach mode: value dari dashboard SUDAH mach (mis. 0.78), TANPA konversi.
            # IAS mode: value dari dashboard itu knots → convert ke m/s.
            value = float(value) if _ap_spd_mode == 1 else float(value) / 1.94384
        elif cmd == 'ap.hdg': value = deg2rad(value)            # deg → rad

    # ── Battery: kirim ke ui_helper DAN direct switch (path di-resolve live) ──
    if cmd == 'battery' and value is not None:
        v = bool(value)
        ui_info = manifest.get('simulator/ui_helpers/systems/electrical/main_battery_button/on')
        if ui_info: send_raw(pack_set_b(ui_info['id'], v))
        if cmd in set_state:
            sid, dtype, _ = set_state[cmd]
            send_raw(pack_set(sid, dtype, int(v)))
        return

    # ── APU: kirim ke ui_helper DAN direct switch (path di-resolve live) ──────
    if cmd == 'apu' and value is not None:
        v = bool(value)
        ui_info = manifest.get('simulator/ui_helpers/systems/electrical/apu_button/on')
        if ui_info: send_raw(pack_set_b(ui_info['id'], v))
        if cmd in set_state:
            sid, dtype, _ = set_state[cmd]
            send_raw(pack_set(sid, dtype, int(v)))
        return

    # ── Engines: START = simulasi tekan-LEPAS start_button (bukan tahan terus) ──
    # STOP = dual-path (ui_helper 'on'=False DAN paksa state=0 langsung).
    if cmd in ('engine1.on', 'engine2.on'):
        eng_n = 0 if cmd == 'engine1.on' else 1
        state_info = manifest.get(f'aircraft/0/systems/engines/{eng_n}/state')
        print(f'ENGINE{eng_n+1} START — state:{bool(state_info)}')
        # FIX: paksa state=2 setelah delay itu MEROSOTIN startup sequence asli
        # yang lagi jalan (game beneran mulai start pas state=1, tapi kepotong
        # sama paksaan kita ke state=2). Sekarang cuma kirim state=1 SEKALI,
        # biarkan game selesain sequence-nya sendiri secara natural.
        if state_info:
            send_raw(pack_set(state_info['id'], DTYPE_INT, 1))   # starting — biarkan game lanjutin sendiri
        return

    if cmd in ('engine1.off', 'engine2.off'):
        eng_n = 0 if cmd == 'engine1.off' else 1
        state_info   = manifest.get(f'aircraft/0/systems/engines/{eng_n}/state')
        mixture_info = manifest.get(f'simulator/ui_helpers/systems/engines/{eng_n}/mixture/on')
        stop_cmd_id, _ = find_run_cmd_id(manifest, ['engine.stop', 'enginestop'])
        print(f'ENGINE{eng_n+1} STOP — state:{bool(state_info)} mixture:{bool(mixture_info)} genericStopCmd:{stop_cmd_id}')
        # Belum ketemu satupun yang confirmed jalan buat stop. Coba SEMUA:
        # (a) mixture cutoff — cara mesin piston biasa dimatiin, siapa tau IF
        #     pakai UI yang sama walau ini jet; (b) generic Engine.Stop RunCommand
        #     yang belum pernah bener2 dites (dulu dihindari krn ambigu, bukan
        #     krn kebukti gagal); (c) repeated state=0 sebagai fallback.
        if mixture_info:
            send_raw(pack_set_b(mixture_info['id'], False))
        if stop_cmd_id:
            send_raw(pack_run(stop_cmd_id))
        if state_info:
            sid = state_info['id']
            def repeat_stop():
                for _ in range(15):   # ~3 detik @ 200ms
                    send_raw(pack_set(sid, DTYPE_INT, 0))
                    time.sleep(0.2)
            threading.Thread(target=repeat_stop, daemon=True).start()
        return

    # ── Trim: SetState langsung ke elevator_trim ────────────────────────────────
    # FIX: raw value IF itu persen×10 (bukan fraction -1..1) — confirmed dari
    # testing user: game -1% = raw -10, game 22% = raw 220, dst. Dashboard kirim
    # persen mentah (mis. 5 buat 5%), di sini dikali 10 biar cocok skala IF.
    if cmd == 'trim' and value is not None:
        ti = manifest.get('aircraft/0/systems/axes/elevator_trim')
        if ti:
            send_raw(pack_set(ti['id'], DTYPE_FLOAT, float(value) * 10))
        return

    # ── PAX set: value = jumlah orang → convert ke berat (×77kg), tulis ke
    # load item yang udah diklasifikasi 'pax' dari slow-poll terakhir ────────────
    if cmd == 'pax.set' and value is not None:
        idxs = st.get('pax_load_idx', [])
        if not idxs:
            print('pax.set: belum ada index PAX terdeteksi (tunggu slow-poll pertama jalan)')
            return
        target_weight = float(value) * 77
        per_item = target_weight / len(idxs)
        for i in idxs:
            info = manifest.get(f'aircraft/0/systems/load/{i}/mass')
            if info: send_raw(pack_set(info['id'], DTYPE_FLOAT, per_item))
        return

    # ── Cargo set: value = kg, tulis langsung ke load item 'cargo' ──────────────
    if cmd == 'cargo.set' and value is not None:
        idxs = st.get('cargo_load_idx', [])
        if not idxs:
            print('cargo.set: belum ada index Cargo terdeteksi (tunggu slow-poll pertama jalan)')
            return
        per_item = float(value) / len(idxs)
        for i in idxs:
            info = manifest.get(f'aircraft/0/systems/load/{i}/mass')
            if info: send_raw(pack_set(info['id'], DTYPE_FLOAT, per_item))
        return

    # ── Fuel set: value = kg TOTAL, dibagi rata ke tank/0 dan tank/1 ────────────
    if cmd == 'fuel.set' and value is not None:
        t0 = manifest.get('aircraft/0/systems/fuel/tank/0/fuel_remaining')
        t1 = manifest.get('aircraft/0/systems/fuel/tank/1/fuel_remaining')
        half = float(value) / 2
        if t0: send_raw(pack_set(t0['id'], DTYPE_FLOAT, half))
        if t1: send_raw(pack_set(t1['id'], DTYPE_FLOAT, half))
        return

    # ── RunCommand (ID di-resolve dinamis dari manifest live) ─────────────────
    if cmd in run_cmd:
        send_raw(pack_run(run_cmd[cmd]))
        return

    # ── SetState lainnya (ID di-resolve dinamis dari manifest live) ───────────
    if cmd in set_state:
        sid, dtype, fixed = set_state[cmd]
        actual = fixed if fixed is not None else value
        if actual is None: return
        if cmd == 'ext.power': actual = int(bool(actual))  # int 0/1
        send_raw(pack_set(sid, dtype, actual))
        return

    print(f'Unknown/unresolved cmd: {cmd} (cek log connect — mungkin path tidak ada di manifest sesi ini)')

# ── HTTP ──────────────────────────────────────────────────────────────────────
@app.route('/')
def index(): return send_file('dashboard.html')

@app.route('/api/status')
def api_status(): return jsonify({'connected': st['connected'], 'ip': st['if_ip']})

if __name__ == '__main__':
    print('=' * 52)
    print('  IF Dashboard v7  (unit fix: aviation units)')
    print('=' * 52)
    print('http://localhost:5000')
    print('=' * 52)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, use_reloader=False, log_output=True)
