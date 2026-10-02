import json
import os
import secrets
import time
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config.settings import SHEET_AUTH_STATE, SHEET_CUTI, SHEET_HARI_LIBUR, SHEET_KARYAWAN, SHEET_KABID_KASI


class SheetNotFoundError(Exception):
    """Raised when a requested worksheet is not found in the spreadsheet."""
    pass

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# === Client cache (singleton) ===
_client_cache = {"client": None, "ts": 0}
CLIENT_TTL = 3600  # re-auth max once per hour


def get_sheets_client():
    now = time.time()
    if _client_cache["client"] and (now - _client_cache["ts"]) < CLIENT_TTL:
        return _client_cache["client"]

    creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    creds = Credentials.from_service_account_info(creds_json, scopes=SCOPES)
    client = gspread.authorize(creds)
    _client_cache["client"] = client
    _client_cache["ts"] = now
    return client


# === Worksheet cache ===
_worksheet_cache = {"sheets": {}, "ts": 0}
WORKSHEET_TTL = 300  # re-open worksheet max every 5 min


def get_sheet(sheet_name):
    from config.settings import SPREADSHEET_ID

    now = time.time()
    cache_key = sheet_name
    cached = _worksheet_cache["sheets"].get(cache_key)
    if cached and (now - _worksheet_cache["ts"]) < WORKSHEET_TTL:
        return cached

    client = get_sheets_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    try:
        sheet = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        raise SheetNotFoundError(
            f"Sheet '{sheet_name}' tidak ditemukan. "
            "Pastikan nama sheet benar di config/settings.py"
        )
    _worksheet_cache["sheets"][cache_key] = sheet
    _worksheet_cache["ts"] = now
    return sheet


# === Data cache ===
_data_cache = {}
DATA_TTL = 10  # cache data for 10 seconds (fast enough for live, slow enough to save API calls)


def _get_cached_data(sheet_name):
    now = time.time()
    cached = _data_cache.get(sheet_name)
    if cached and (now - cached["ts"]) < DATA_TTL:
        return cached["data"]

    sheet = get_sheet(sheet_name)
    head_row = 2 if sheet_name == SHEET_KARYAWAN else 1
    try:
        data = sheet.get_all_records(head=head_row)
    except Exception:
        all_values = sheet.get_all_values()
        if not all_values:
            data = []
        else:
            raw_headers = all_values[head_row - 1]
            seen = {}
            headers = []
            for h in raw_headers:
                h = h.strip()
                if not h:
                    continue
                if h in seen:
                    seen[h] += 1
                    headers.append(f"{h}_{seen[h]}")
                else:
                    seen[h] = 0
                    headers.append(h)
            data = []
            for row in all_values[head_row:]:
                row_padded = row + [""] * (len(headers) - len(row))
                data.append(dict(zip(headers, row_padded)))
    _data_cache[sheet_name] = {"data": data, "ts": now}
    return data


def invalidate_cache(sheet_name=None):
    """Call after writes to force fresh reads."""
    if sheet_name:
        _data_cache.pop(sheet_name, None)
    else:
        _data_cache.clear()
    # Also force worksheet re-fetch after writes
    _worksheet_cache["ts"] = 0


def get_all_records(sheet_name):
    return _get_cached_data(sheet_name)


def get_all_kabid_kasi():
    """Mengambil daftar nama dan NIP KABID/KASI."""
    try:
        return get_all_records(SHEET_KABID_KASI)
    except SheetNotFoundError:
        return []


def get_kabid_kasi_by_nama(nama):
    """Mencari NIP KABID/KASI berdasarkan nama."""
    target = str(nama).strip().casefold()
    for row in get_all_kabid_kasi():
        if str(row.get("NAMA", "")).strip().casefold() == target:
            return dict(row)
    return None


def get_karyawan_by_nip(nip):
    records = get_all_records(SHEET_KARYAWAN)
    for r in records:
        if str(r.get("NI PPPK PW", "")).strip() == str(nip).strip():
            r = dict(r)  # copy to avoid mutating cached data
            nip_str = str(nip).strip()
            if len(nip_str) >= 8:
                r["TGL_LAHIR"] = f"{nip_str[:4]}-{nip_str[4:6]}-{nip_str[6:8]}"
            else:
                r["TGL_LAHIR"] = ""
            return r
    return None


def get_pengajuan_by_nama(nama):
    records = get_all_records(SHEET_CUTI)
    return [r for r in records if str(r.get("NAMA", "")).strip().casefold() == str(nama).strip().casefold()]


def get_pengajuan_by_status(status_filter=None, bulan_filter=None, seksi_filter=None):
    records = get_all_records(SHEET_CUTI)
    result = []
    for r in records:
        if status_filter and status_filter != "Semua":
            if r.get("STATUS", "").strip() != status_filter:
                continue
        if bulan_filter:
            if bulan_filter.lower() not in str(r.get("MASEHI", "")).lower():
                continue
        if seksi_filter:
            if r.get("SEKSI", "").strip() != seksi_filter:
                continue
        result.append(r)
    return result


def append_row(sheet_name, data: dict, head_row=1):
    sheet = get_sheet(sheet_name)
    headers = [h.strip() for h in sheet.row_values(head_row)]

    # Auto-add missing column headers (e.g. DURASI_HARI_KERJA) to row 1
    missing = [k for k in data.keys() if k not in headers]
    if missing:
        for m in missing:
            headers.append(m)
            sheet.update_cell(head_row, len(headers), m)

    row = [data.get(h, "") for h in headers]
    sheet.append_row(row)
    invalidate_cache(sheet_name)
    return None


def update_cell(sheet_name, row_num, col_name, value):
    sheet = get_sheet(sheet_name)
    headers = sheet.row_values(1)
    if col_name not in headers:
        raise ValueError(f"Kolom '{col_name}' tidak ditemukan")
    col_index = headers.index(col_name) + 1
    sheet.update_cell(row_num, col_index, value)
    invalidate_cache(sheet_name)


def generate_pengajuan_id():
    return secrets.token_urlsafe(9)


def get_pengajuan_by_id(pengajuan_id):
    pengajuan_id = str(pengajuan_id).strip()
    for r in get_all_records(SHEET_CUTI):
        if str(r.get("ID", "")).strip() == pengajuan_id:
            return dict(r)  # copy to avoid exposing cached dict
    return None


def update_status_by_id(sheet_name, pengajuan_id, status, no_surat=None):
    """Update STATUS (and optionally NO SURAT) of the record with this ID.

    The sheet row is resolved at WRITE time by scanning the ID column, so a
    shifted/edited sheet can never cause a write to the wrong record.
    """
    pengajuan_id = str(pengajuan_id).strip()
    invalidate_cache(sheet_name)
    sheet = get_sheet(sheet_name)
    headers = [h.strip() for h in sheet.row_values(1)]
    if "ID" not in headers:
        raise ValueError("Kolom 'ID' tidak ditemukan. Jalankan migrate_add_id.py dulu.")
    id_col = headers.index("ID") + 1

    id_values = sheet.col_values(id_col)  # index 0 = header
    try:
        row_num = id_values.index(pengajuan_id) + 1
    except ValueError:
        raise ValueError(f"Pengajuan dengan ID '{pengajuan_id}' tidak ditemukan.")

    update_cell(sheet_name, row_num, "STATUS", status)
    if no_surat is not None and str(no_surat).strip():
        update_cell(sheet_name, row_num, "NO SURAT", str(no_surat).strip())


def get_all_seksi(sheet_name=None):
    if sheet_name is None:
        sheet_name = SHEET_CUTI
    records = get_all_records(sheet_name)
    return sorted(set(r.get("SEKSI", "").strip() for r in records if r.get("SEKSI", "").strip()))


def get_stats():
    records = get_all_records(SHEET_CUTI)
    menunggu = sum(1 for r in records if r.get("STATUS", "").strip() == "Menunggu ACC")
    disetujui = sum(1 for r in records if r.get("STATUS", "").strip() == "Disetujui")
    ditolak = sum(1 for r in records if r.get("STATUS", "").strip() == "Ditolak")
    return {"menunggu": menunggu, "disetujui": disetujui, "ditolak": ditolak, "total": len(records)}


# === Hari Libur ===
_libur_cache = {"data": None, "ts": 0}
LIBUR_TTL = 86400  # 24 hours


def get_hari_libur_set():
    """Mengembalikan set of string TANGGAL hari libur (YYYY-MM-DD)."""
    now = time.time()
    if _libur_cache["data"] is not None and (now - _libur_cache["ts"]) < LIBUR_TTL:
        return _libur_cache["data"]

    try:
        sheet = get_sheet(SHEET_HARI_LIBUR)
        # Skip header
        records = sheet.get_all_records(head=1)
        libur_set = set(str(r.get("TANGGAL", "")).strip() for r in records if r.get("TANGGAL", ""))
        _libur_cache["data"] = libur_set
        _libur_cache["ts"] = now
        return libur_set
    except SheetNotFoundError:
        return set()
    except Exception:
        return set()


def invalidate_hari_libur_cache():
    _libur_cache["data"] = None
    _libur_cache["ts"] = 0
    invalidate_cache(SHEET_HARI_LIBUR)


def get_all_hari_libur():
    """Mengambil semua record hari libur untuk admin (list of dict)."""
    try:
        return get_all_records(SHEET_HARI_LIBUR)
    except SheetNotFoundError:
        return []


def delete_hari_libur_by_tanggal(tanggal: str):
    """Menghapus baris hari libur berdasarkan TANGGAL yang cocok."""
    sheet = get_sheet(SHEET_HARI_LIBUR)
    # Col 1 is assumed to be TANGGAL (as per setup_sheets.py)
    # Check headers first
    headers = [h.strip() for h in sheet.row_values(1)]
    if "TANGGAL" not in headers:
        raise ValueError("Kolom 'TANGGAL' tidak ditemukan di sheet HARI_LIBUR")
    
    col_idx = headers.index("TANGGAL") + 1
    col_values = sheet.col_values(col_idx)
    
    # Cari index yang cocok (bisa ada duplikat tanggal, hapus semua)
    # Lakukan dari bawah ke atas agar tidak mengubah index baris di atasnya
    deleted = 0
    for i in range(len(col_values), 1, -1):  # row 1 is header
        if col_values[i-1].strip() == tanggal.strip():
            sheet.delete_rows(i)
            deleted += 1
            
    if deleted > 0:
        invalidate_hari_libur_cache()
    else:
        raise ValueError(f"Tanggal '{tanggal}' tidak ditemukan.")


# === Auth State (Lockout) via Google Sheets ===

_AUTH_HEADERS = ["USERNAME", "COUNT", "FIRST_ATTEMPT"]


def _auth_sheet_indexes(sheet):
    """Return (user_col, count_col, ts_col) 1-based column indexes from headers."""
    headers = [h.strip().upper() for h in sheet.row_values(1)]
    # Normalize common typo FIRTS_ATTEMPT -> FIRST_ATTEMPT
    headers = ["FIRST_ATTEMPT" if h == "FIRTS_ATTEMPT" else h for h in headers]
    # Normalize IP -> USERNAME (legacy migration)
    headers = ["USERNAME" if h == "IP" else h for h in headers]
    try:
        return headers.index("USERNAME") + 1, headers.index("COUNT") + 1, headers.index("FIRST_ATTEMPT") + 1
    except ValueError:
        raise ValueError("Kolom AUTH_STATE harus: USERNAME, COUNT, FIRST_ATTEMPT")


def get_auth_state(username: str):
    """Get lockout state for a username. Returns dict {count, first_attempt} or None."""
    try:
        sheet = get_sheet(SHEET_AUTH_STATE)
        records = sheet.get_all_records(head=1)
        for r in records:
            # Match USERNAME or legacy IP column
            val = str(r.get("USERNAME", r.get("IP", ""))).strip()
            if val == username.strip():
                raw_ts = r.get("FIRST_ATTEMPT", r.get("FIRTS_ATTEMPT", 0))
                try:
                    first_attempt = float(raw_ts)
                except (TypeError, ValueError):
                    first_attempt = 0.0
                if first_attempt <= 0:
                    return None  # corrupt row — treat as no state
                return {
                    "count": int(r.get("COUNT", 0)),
                    "first_attempt": first_attempt,
                }
        return None
    except (SheetNotFoundError, Exception):
        return None


def set_auth_state(username: str, count: int, first_attempt: float):
    """Write or update lockout state for a username."""
    try:
        sheet = get_sheet(SHEET_AUTH_STATE)
        user_col, count_col, ts_col = _auth_sheet_indexes(sheet)
        records = sheet.get_all_records(head=1)

        # Find existing row
        for i, r in enumerate(records, start=2):  # row 2 = first data row
            val = str(r.get("USERNAME", r.get("IP", ""))).strip()
            if val == username.strip():
                sheet.update_cell(i, count_col, count)
                # Store as string to avoid Google Sheets float truncation
                sheet.update_cell(i, ts_col, str(first_attempt))
                return

        # Not found — append new row
        sheet.append_row([username, count, str(first_attempt)])
    except (SheetNotFoundError, Exception):
        pass  # Sheets down/inaccessible — login tetap jalan tanpa lockout persist


def delete_auth_state(username: str):
    """Remove lockout row for a username."""
    try:
        sheet = get_sheet(SHEET_AUTH_STATE)
        user_col, count_col, ts_col = _auth_sheet_indexes(sheet)
        records = sheet.get_all_records(head=1)
        for i, r in enumerate(records, start=2):
            val = str(r.get("USERNAME", r.get("IP", ""))).strip()
            if val == username.strip():
                sheet.delete_rows(i)
                return
    except (SheetNotFoundError, Exception):
        pass
