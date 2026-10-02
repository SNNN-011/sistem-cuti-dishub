"""Jalankan app lokal dengan koneksi NYATA ke Google Sheets.

Bedanya dengan run_local.py:
  - run_local.py  -> semua data di-MOCK (dummy, nggak nyantol ke Sheets)
  - file ini      -> beneran baca/tulis ke Google Sheets asli

.env otomatis kebaca oleh config/settings.py (load_dotenv), jadi tidak perlu
set environment variable manual.

Yang diubah dari app.py produksi:
  - SESSION_COOKIE_SECURE = False, karena localhost itu HTTP. Flag ini
    dipaksa True di app.py:25 untuk keamanan produksi (HTTPS only), tapi
    kalau True di localhost, browser tidak akan mengirim cookie session
    dan login akan selalu gagal / loop balik ke halaman login.

Jalankan:
  venv\\Scripts\\python.exe run_sheets.py
"""
import os

# Wajib import SEBELUM app di-import, karena config/settings.py memanggil
# load_dotenv() saat di-import dan membaca nilainya saat itu juga.
from dotenv import load_dotenv

load_dotenv()

# Fail fast dengan pesan jelas, jangan sampai error cryptic saat start.
required = [
    "SECRET_KEY",
    "SPREADSHEET_ID",
    "GOOGLE_CREDENTIALS_JSON",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD_HASH",
]
missing = [k for k in required if not os.environ.get(k)]
if missing:
    raise SystemExit(
        "Environment variable belum di-set: " + ", ".join(missing)
        + "\nIsi .env terlebih dahulu (lihat .env.example)."
    )

from app import create_app  # noqa: E402

app = create_app()

# Lokal = HTTP, jadi cookie Secure harus dimatikan (lihat docstring di atas).
app.config["SESSION_COOKIE_SECURE"] = False

if __name__ == "__main__":
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", 5000))
    print(f"Connecting to real Google Sheets (SPREADSHEET_ID="
          f"{os.environ['SPREADSHEET_ID'][:6]}...)")
    print(f"Running on http://{host}:{port}")
    print("Login admin:", os.environ["ADMIN_USERNAME"])
    app.run(host=host, port=port, debug=False, use_reloader=False)
