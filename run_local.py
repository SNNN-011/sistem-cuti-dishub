"""Local runner dengan mock Sheets agar bisa lihat logo tanpa butuh koneksi Sheets."""
import os, json
# --- env untuk lokal (tidak perlu .env file) ---
os.environ["SECRET_KEY"] = "test_local_secret_key_32_chars_min_12345"
# ambil kredensial asli biar tidak error format, tapi spreadsheet dimock
with open("sistem-cuti-dishub-a0943db153bf.json", encoding="utf-8") as f:
    os.environ["GOOGLE_CREDENTIALS_JSON"] = f.read()
os.environ["SPREADSHEET_ID"] = "mock_spreadsheet_id"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD_HASH"] = "$2b$12$dummyhashfortestonly"

# --- mock sheets sebelum app import ---
import services.sheets_service as ss
def _mock_records(sheet): return []
def _mock_karyawan(nip): return {"NAMA":"Test User","NIP":nip,"TGL_LAHIR":"2000-01-01"} if nip=="123456" else None
def _mock_kabid_list(): return [{"NAMA":"Kabid Test","NIP":"999"}]
def _mock_kabid_by_nama(nama): return {"NAMA":nama,"NIP":"999"}
def _mock_append(*a,**kw): print("[MOCK] append_row", a[0])
ss.get_all_records = _mock_records
ss.get_karyawan_by_nip = _mock_karyawan
ss.get_all_kabid_kasi = _mock_kabid_list
ss.get_kabid_kasi_by_nama = _mock_kabid_by_nama
ss.append_row = _mock_append
ss.get_pengajuan_by_nama = lambda nama: []
ss.get_all_kabid_kasi = _mock_kabid_list

from app import create_app
app = create_app()
app.config["SESSION_COOKIE_SECURE"] = False

if __name__ == "__main__":
    print("Running on http://127.0.0.1:5000  (mock Sheets, logo 32px)")
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
