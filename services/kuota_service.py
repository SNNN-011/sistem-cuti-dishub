"""Kuota cuti service — hitung pemakaian & sisa kuota per karyawan per tahun.

Kuota dihitung dalam satuan **hari kerja** (Senin–Jumat).
- Kuota tahunan: 12 hari kerja (tidak termasuk Sakit & Cuti Hamil).
- Kuota cuti hamil/melahirkan: 90 hari kerja, terpisah dari kuota tahunan.
"""

import re
from datetime import datetime, timedelta

from config.settings import KUOTA_TAHUNAN, SHEET_CUTI
from services.sheets_service import get_all_records, get_hari_libur_set

KUOTA_HAMIL = 90  # hari kerja

MONTHS_ID = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
}


def hitung_hari_kerja(tgl_mulai_str: str, tgl_selesai_str: str) -> int:
    """Hitung jumlah hari kerja (Senin–Jumat) antara dua tanggal (inklusif).

    Menerima format YYYY-MM-DD. Return 0 jika format tidak valid.
    """
    try:
        mulai = datetime.strptime(str(tgl_mulai_str).strip()[:10], "%Y-%m-%d").date()
        selesai = datetime.strptime(str(tgl_selesai_str).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 0

    if selesai < mulai:
        return 0

    libur_set = get_hari_libur_set()
    count = 0
    current = mulai
    while current <= selesai:
        if current.weekday() < 5 and current.strftime("%Y-%m-%d") not in libur_set:  # 0=Senin, 4=Jumat
            count += 1
        current += timedelta(days=1)
    return count


def tambah_hari_kerja(tgl_mulai_str: str, hari_kerja_ditambahkan: int) -> str:
    """Mengembalikan string YYYY-MM-DD setelah ditambahkan sejumlah hari kerja."""
    try:
        mulai = datetime.strptime(str(tgl_mulai_str).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return ""

    if hari_kerja_ditambahkan <= 0:
        return ""

    libur_set = get_hari_libur_set()
    count = 1
    current = mulai
    
    if current.weekday() >= 5 or current.strftime("%Y-%m-%d") in libur_set:
        count = 0  # Kalau hari pertama libur, belum dihitung

    while count < hari_kerja_ditambahkan:
        current += timedelta(days=1)
        if current.weekday() < 5 and current.strftime("%Y-%m-%d") not in libur_set:
            count += 1
            
    return current.strftime("%Y-%m-%d")


def parse_hari_str(hari_str: str) -> int:
    """Parse string HARI (misal '11 s.d. 13 Agustus 2026') dan hitung hari kerjanya."""
    s = str(hari_str).strip()
    if not s:
        return 1

    # Pattern 1: '11 s.d. 13 Agustus 2026'
    m = re.match(r"^(\d+)\s+s\.d\.\s+(\d+)\s+([A-Za-z]+)\s+(\d{4})$", s, re.IGNORECASE)
    if m:
        try:
            day1, day2, m_str, y_str = int(m.group(1)), int(m.group(2)), m.group(3).lower(), int(m.group(4))
            if m_str in MONTHS_ID:
                m_num = MONTHS_ID[m_str]
                d1 = datetime(y_str, m_num, day1).date()
                d2 = datetime(y_str, m_num, day2).date()
                libur_set = get_hari_libur_set()
                count = 0
                cur = d1
                while cur <= d2:
                    if cur.weekday() < 5 and cur.strftime("%Y-%m-%d") not in libur_set:
                        count += 1
                    cur += timedelta(days=1)
                return max(count, 1)
        except Exception:
            pass

    # Pattern 2: '28 Juli 2026 s.d. 2 Agustus 2026'
    m = re.match(r"^(\d+)\s+([A-Za-z]+)\s+(\d{4})\s+s\.d\.\s+(\d+)\s+([A-Za-z]+)\s+(\d{4})$", s, re.IGNORECASE)
    if m:
        try:
            day1, m1_str, y1_str = int(m.group(1)), m.group(2).lower(), int(m.group(3))
            day2, m2_str, y2_str = int(m.group(4)), m.group(5).lower(), int(m.group(6))
            if m1_str in MONTHS_ID and m2_str in MONTHS_ID:
                d1 = datetime(y1_str, MONTHS_ID[m1_str], day1).date()
                d2 = datetime(y2_str, MONTHS_ID[m2_str], day2).date()
                libur_set = get_hari_libur_set()
                count = 0
                cur = d1
                while cur <= d2:
                    if cur.weekday() < 5 and cur.strftime("%Y-%m-%d") not in libur_set:
                        count += 1
                    cur += timedelta(days=1)
                return max(count, 1)
        except Exception:
            pass

    # Pattern 3: '11 Agustus 2026'
    m = re.match(r"^(\d+)\s+([A-Za-z]+)\s+(\d{4})$", s, re.IGNORECASE)
    if m:
        try:
            day1, m_str, y_str = int(m.group(1)), m.group(2).lower(), int(m.group(3))
            if m_str in MONTHS_ID:
                d1 = datetime(y_str, MONTHS_ID[m_str], day1).date()
                libur_set = get_hari_libur_set()
                is_kerja = (d1.weekday() < 5) and (d1.strftime("%Y-%m-%d") not in libur_set)
                return 1 if is_kerja else 0
        except Exception:
            pass

    return 1


def _get_durasi(row: dict) -> int:
    """Ambil durasi hari kerja dari row Sheets.

    Prioritas:
    1. Kolom DURASI_HARI_KERJA
    2. Parsing dari string HARI (misal '11 s.d. 13 Agustus 2026')
    """
    durasi_raw = row.get("DURASI_HARI_KERJA", "")
    if durasi_raw not in ("", None, 0, "0"):
        try:
            return int(durasi_raw)
        except (ValueError, TypeError):
            pass

    # Fallback: parse string HARI
    hari_str = row.get("HARI", "")
    if hari_str:
        return parse_hari_str(hari_str)

    return 1


def hitung_kuota_terpakai(nama: str, tahun: int) -> int:
    """Hitung total hari kerja cuti tahunan terpakai untuk NAMA di tahun tertentu.

    Exclude: Sakit, Cuti Hamil/Melahirkan (punya kuota terpisah).
    """
    semua_data = get_all_records(SHEET_CUTI)
    total = 0
    for row in semua_data:
        if str(row.get("NAMA", "")).strip().casefold() != str(nama).strip().casefold():
            continue
        if str(row.get("TAHUN", "")) != str(tahun):
            continue
        if row.get("STATUS", "").strip() != "Disetujui":
            continue
        keperluan = str(row.get("KEPERLUAN", "")).strip().upper()
        if keperluan in ("SAKIT", "CUTI HAMIL/MELAHIRKAN", "CUTI MELAHIRKAN"):
            continue
        total += _get_durasi(row)
    return total


def sisa_kuota(nama: str, tahun: int) -> int:
    """Sisa kuota cuti tahunan dalam hari kerja."""
    sisa = KUOTA_TAHUNAN - hitung_kuota_terpakai(nama, tahun)
    return max(sisa, 0)


def boleh_ajukan(nama: str, tahun: int, keperluan: str = "", durasi: int = 1) -> bool:
    """Cek apakah masih boleh mengajukan cuti."""
    kep = str(keperluan).strip().upper()
    if kep == "SAKIT":
        return True  # sakit tidak pakai kuota

    if kep in ("CUTI HAMIL/MELAHIRKAN", "CUTI MELAHIRKAN"):
        sisa = sisa_kuota_hamil(nama, tahun)
        return sisa >= durasi

    return sisa_kuota(nama, tahun) >= durasi


# ── Kuota Cuti Hamil ──────────────────────────────────────────────────

def hitung_kuota_hamil_terpakai(nama: str, tahun: int) -> int:
    """Hitung total hari kerja cuti hamil/melahirkan terpakai."""
    semua_data = get_all_records(SHEET_CUTI)
    total = 0
    for row in semua_data:
        if str(row.get("NAMA", "")).strip().casefold() != str(nama).strip().casefold():
            continue
        if str(row.get("TAHUN", "")) != str(tahun):
            continue
        if row.get("STATUS", "").strip() != "Disetujui":
            continue
        if str(row.get("KEPERLUAN", "")).strip().upper() not in ("CUTI HAMIL/MELAHIRKAN", "CUTI MELAHIRKAN"):
            continue
        total += _get_durasi(row)
    return total


def sisa_kuota_hamil(nama: str, tahun: int) -> int:
    """Sisa kuota cuti hamil dalam hari kerja."""
    sisa = KUOTA_HAMIL - hitung_kuota_hamil_terpakai(nama, tahun)
    return max(sisa, 0)


def get_tahun_sekarang() -> int:
    return datetime.now().year
