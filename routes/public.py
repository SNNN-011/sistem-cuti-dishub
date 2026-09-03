from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from config.constants import BULAN_NAMA
from config.settings import SHEET_CUTI

from services.kuota_service import (
    boleh_ajukan,
    get_tahun_sekarang,
    hitung_hari_kerja,
    sisa_kuota,
    sisa_kuota_hamil,
    tambah_hari_kerja,
)
from services.security import rate_limit, safe_error_message, validate_csrf
from services.sheets_service import (
    append_row,
    generate_pengajuan_id,
    get_all_records,
    get_karyawan_by_nip,
    get_pengajuan_by_nama,
    get_all_kabid_kasi,
    get_kabid_kasi_by_nama,
)

public_bp = Blueprint("public", __name__)


@public_bp.route("/", methods=["GET", "POST"])
@rate_limit(max_requests=10, window_seconds=3600, methods=["POST"])  # 10 submit per jam per IP (GET not limited)
def form_cuti():
    if request.method == "POST":
        validate_csrf()  # CSRF check

        ni_pppk_pw = request.form.get("ni_pppk_pw", "").strip()
        nama = request.form.get("nama", "").strip()
        jabatan = request.form.get("jabatan", "").strip()
        seksi = request.form.get("seksi", "").strip()
        shif = request.form.get("shif", "").strip()
        tgl_mulai = request.form.get("tgl_mulai", "").strip()
        tgl_selesai = request.form.get("tgl_selesai", "").strip()
        keperluan = request.form.get("keperluan", "").strip().upper()
        kabid_kasi = request.form.get("kabid_kasi", "").strip()
        catatan = request.form.get("catatan", "").strip()

        # Normalisasi: KEPERLUAN selalu kapital (legacy data case-insensitive)
        KEPERLUAN_HAMIL = {"CUTI HAMIL/MELAHIRKAN", "CUTI MELAHIRKAN"}

        # Validasi field wajib
        missing = []
        if not ni_pppk_pw: missing.append("NI PPPK PW")
        if not nama: missing.append("Nama")
        if not jabatan: missing.append("Jabatan")
        if not seksi: missing.append("Bidang/Seksi")
        if not shif: missing.append("Shif")
        if not tgl_mulai: missing.append("Tanggal Mulai")
        if not tgl_selesai and keperluan not in KEPERLUAN_HAMIL:
            missing.append("Tanggal Selesai")
        if not keperluan: missing.append("Keperluan")
        if not kabid_kasi: missing.append("Kabid/Kasi")
        if missing:
            flash(f"Field wajib belum diisi: {', '.join(missing)}.", "danger")
            return render_template("form_cuti.html", form_data=request.form, kabid_kasi_list=get_all_kabid_kasi())

        # Validasi NI PPPK PW format (hanya angka)
        if not ni_pppk_pw.isdigit():
            flash("NI PPPK PW harus berupa angka.", "danger")
            return render_template("form_cuti.html", form_data=request.form, kabid_kasi_list=get_all_kabid_kasi())

        # Validasi NI PPPK PW terdaftar
        karyawan = get_karyawan_by_nip(ni_pppk_pw)
        if not karyawan:
            flash("NI PPPK PW tidak terdaftar di database karyawan.", "danger")
            return render_template("form_cuti.html", form_data=request.form, kabid_kasi_list=get_all_kabid_kasi())

        # Ambil NIP KABID/KASI dari sheet master
        kabid_data = get_kabid_kasi_by_nama(kabid_kasi)
        if not kabid_data or not str(kabid_data.get("NIP", "")).strip():
            flash("NIP KABID/KASI tidak ditemukan.", "danger")
            return render_template("form_cuti.html", form_data=request.form, kabid_kasi_list=get_all_kabid_kasi())
        nip_kabid = str(kabid_data["NIP"]).strip()

        # Override tgl_selesai untuk Cuti Melahirkan (selalu hitung akurat dari server)
        if keperluan in KEPERLUAN_HAMIL:
            tgl_selesai = tambah_hari_kerja(tgl_mulai, 90)

        # Validasi tanggal
        try:
            tgl_mulai_dt = datetime.strptime(tgl_mulai, "%Y-%m-%d")
            tgl_selesai_dt = datetime.strptime(tgl_selesai, "%Y-%m-%d")
            if tgl_selesai_dt < tgl_mulai_dt:
                flash("Tanggal selesai tidak boleh sebelum tanggal mulai.", "danger")
                return render_template("form_cuti.html", form_data=request.form, kabid_kasi_list=get_all_kabid_kasi())
        except ValueError:
            flash("Format tanggal tidak valid.", "danger")
            return render_template("form_cuti.html", form_data=request.form, kabid_kasi_list=get_all_kabid_kasi())

        # Hitung durasi hari kerja
        durasi_hari_kerja = hitung_hari_kerja(tgl_mulai, tgl_selesai)
        if durasi_hari_kerja <= 0:
            flash("Durasi cuti tidak valid (0 hari kerja).", "danger")
            return render_template("form_cuti.html", form_data=request.form, kabid_kasi_list=get_all_kabid_kasi())

        # Validasi kuota
        tahun = get_tahun_sekarang()
        if not boleh_ajukan(nama, tahun, keperluan, durasi_hari_kerja):
            if keperluan in KEPERLUAN_HAMIL:
                flash("Kuota cuti hamil/melahirkan (90 hari kerja) tidak mencukupi.", "danger")
            else:
                flash("Kuota cuti tahunan (12 hari kerja) tidak mencukupi.", "danger")
            return render_template("form_cuti.html", form_data=request.form, kabid_kasi_list=get_all_kabid_kasi())

        # Format hari
        if tgl_mulai_dt.date() == tgl_selesai_dt.date():
            hari = f"{tgl_mulai_dt.day} {BULAN_NAMA[tgl_mulai_dt.month]} {tgl_mulai_dt.year}"
        elif tgl_mulai_dt.month != tgl_selesai_dt.month or tgl_mulai_dt.year != tgl_selesai_dt.year:
            # Cross-month or cross-year: show full dates for both
            hari = (
                f"{tgl_mulai_dt.day} {BULAN_NAMA[tgl_mulai_dt.month]} {tgl_mulai_dt.year} "
                f"s.d. {tgl_selesai_dt.day} {BULAN_NAMA[tgl_selesai_dt.month]} {tgl_selesai_dt.year}"
            )
        else:
            hari = (
                f"{tgl_mulai_dt.day} s.d. {tgl_selesai_dt.day} "
                f"{BULAN_NAMA[tgl_selesai_dt.month]} {tgl_selesai_dt.year}"
            )

        bulan_str = f"{BULAN_NAMA[tgl_mulai_dt.month]} {tgl_mulai_dt.year}"

        # Hitung NO auto-increment (kolom NO di Sheets CUTI 2026)
        try:
            records = get_all_records(SHEET_CUTI)
            max_no = 0
            for r in records:
                try:
                    n = int(str(r.get("NO", "")).strip())
                    if n > max_no:
                        max_no = n
                except (ValueError, TypeError):
                    continue
            next_no = str(max_no + 1)
        except Exception:
            next_no = "1"

        # Tulis ke Sheets
        data = {
            "NO": next_no,
            "MASEHI": bulan_str,
            "HARI": hari,
            "NAMA": nama,
            "KEPERLUAN": keperluan,
            "NO SURAT": "",
            "JABATAN": jabatan,
            "SEKSI": seksi,
            "SHIF": shif,
            "KABID/KASI": kabid_kasi,
            "NIP": nip_kabid,
            "STATUS": "Menunggu ACC",
            "TGL_SUBMIT": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "TAHUN": str(tahun),
            "ID": generate_pengajuan_id(),
            "CATATAN": catatan,
            "DURASI_HARI_KERJA": str(durasi_hari_kerja),
        }

        try:
            append_row(SHEET_CUTI, data)
            flash("Pengajuan berhasil dikirim!", "success")
            return redirect(url_for("public.form_cuti"))
        except Exception as e:
            flash(safe_error_message(e, "mengirim pengajuan"), "danger")
            return render_template("form_cuti.html", form_data=request.form, kabid_kasi_list=get_all_kabid_kasi())

    return render_template("form_cuti.html", form_data={}, kabid_kasi_list=get_all_kabid_kasi())


@public_bp.route("/api/karyawan/validate/<ni_pppk_pw>")
@rate_limit(max_requests=30, window_seconds=60)
def api_validate_ni_pppk_pw(ni_pppk_pw):
    """Validasi NI PPPK PW ada. Selalu HTTP 200 supaya tidak bisa di-enumerate."""
    nip_clean = ni_pppk_pw.strip()
    if not nip_clean.isdigit() or len(nip_clean) > 20:
        return jsonify({"valid": False, "message": "NI PPPK PW tidak terdaftar."})
    karyawan = get_karyawan_by_nip(nip_clean)
    if not karyawan:
        return jsonify({"valid": False, "message": "NI PPPK PW tidak terdaftar."})
    return jsonify({"valid": True, "message": "NI PPPK PW terdaftar."})


@public_bp.route("/api/hitung-90-hari-kerja")
@rate_limit(max_requests=30, window_seconds=60)
def api_hitung_90_hari():
    """API untuk menghitung tanggal selesai 90 hari kerja (cuti melahirkan)."""
    mulai = request.args.get("mulai", "").strip()
    if not mulai:
        return jsonify({"selesai": "", "hari_kerja": 90})
    
    selesai = tambah_hari_kerja(mulai, 90)
    return jsonify({"selesai": selesai, "hari_kerja": 90})




@public_bp.route("/cek-status", methods=["GET", "POST"])
@rate_limit(max_requests=20, window_seconds=60)
def cek_status():
    # Auto-show if redirected from form submission
    if request.method == "GET" and "pending_ni_pppk_pw" in session:
        ni_pppk_pw = session.pop("pending_ni_pppk_pw")
        session.pop("pending_tgl_lahir", None)
        karyawan = get_karyawan_by_nip(ni_pppk_pw)
        if karyawan:
            nama = karyawan.get("NAMA", "")
            pengajuan = get_pengajuan_by_nama(nama)
            tahun = get_tahun_sekarang()
            sisa = sisa_kuota(nama, tahun)
            sisa_hamil = sisa_kuota_hamil(nama, tahun)
            return render_template(
                "cek_status.html",
                pengajuan=pengajuan,
                nama=nama,
                ni_pppk_pw=ni_pppk_pw,
                sisa_kuota=sisa,
                sisa_kuota_hamil=sisa_hamil,
                tahun=tahun,
                submitted=True,
            )

    if request.method == "POST":
        validate_csrf()  # CSRF check

        ni_pppk_pw = request.form.get("ni_pppk_pw", "").strip()
        tgl_lahir = request.form.get("tgl_lahir", "").strip()

        if not ni_pppk_pw or not tgl_lahir:
            flash("NI PPPK PW dan Tanggal Lahir wajib diisi.", "danger")
            return render_template("cek_status.html")

        if not ni_pppk_pw.isdigit():
            flash("NI PPPK PW harus berupa angka.", "danger")
            return render_template("cek_status.html")

        karyawan = get_karyawan_by_nip(ni_pppk_pw)
        if not karyawan:
            flash("NI PPPK PW tidak ditemukan.", "danger")
            return render_template("cek_status.html")

        if str(karyawan.get("TGL_LAHIR", "")).strip() != tgl_lahir:
            flash("Tanggal lahir tidak sesuai.", "danger")
            return render_template("cek_status.html")

        nama = karyawan.get("NAMA", "")
        pengajuan = get_pengajuan_by_nama(nama)
        tahun = get_tahun_sekarang()
        sisa = sisa_kuota(nama, tahun)
        sisa_hamil = sisa_kuota_hamil(nama, tahun)

        return render_template(
            "cek_status.html",
            pengajuan=pengajuan,
            nama=nama,
            ni_pppk_pw=ni_pppk_pw,
            sisa_kuota=sisa,
            sisa_kuota_hamil=sisa_hamil,
            tahun=tahun,
            submitted=True,
        )

    return render_template("cek_status.html", submitted=False)
