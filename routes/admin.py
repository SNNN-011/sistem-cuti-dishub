import io
import re

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from config.settings import ADMIN_USERNAME, KUOTA_TAHUNAN, SHEET_CUTI, SHEET_HARI_LIBUR
from models import AdminUser
from services.auth_service import check_lockout, clear_attempts, record_failed_attempt, verify_password
from services.kuota_service import KUOTA_HAMIL, _get_durasi, get_tahun_sekarang
from services.security import get_real_ip, safe_error_message, validate_csrf
from services.sheets_service import (
    get_all_records,
    get_all_seksi,
    get_pengajuan_by_id,
    get_pengajuan_by_status,
    get_stats,
    update_cell,
    update_status_by_id,
    get_all_hari_libur,
    delete_hari_libur_by_tanggal,
    invalidate_hari_libur_cache,
    append_row,
)
from services.surat_service import generate_surat

admin_bp = Blueprint("admin", __name__, template_folder="../templates/admin")

_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{6,64}")


def _require_valid_id(pengajuan_id):
    if not _ID_PATTERN.fullmatch(pengajuan_id):
        abort(404)


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        validate_csrf()  # CSRF check

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Check lockout per USERNAME (immune to CGNAT IP rotation)
        is_locked, remaining = check_lockout(username)

        if is_locked:
            flash(f"Akun terkunci. Coba lagi dalam {remaining} detik.", "danger")
            return render_template("login.html")

        # Always verify password even if username wrong (timing attack prevention)
        password_ok = verify_password(password)
        username_ok = (username == ADMIN_USERNAME)

        if username_ok and password_ok:
            clear_attempts(username)
            user = AdminUser(username)
            login_user(user, remember=False)
            session.permanent = True
            flash("Login berhasil.", "success")
            return redirect(url_for("admin.dashboard"))
        else:
            record_failed_attempt(username)
            is_locked, remaining = check_lockout(username)
            if is_locked:
                flash(
                    f"Password salah. Akun terkunci selama {remaining} detik.",
                    "danger",
                )
            else:
                flash("Username atau password salah.", "danger")

    return render_template("login.html")


@admin_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    validate_csrf()  # CSRF check
    logout_user()
    flash("Anda telah logout.", "info")
    return redirect(url_for("admin.login"))


@admin_bp.route("/dashboard")
@login_required
def dashboard():
    status_filter = request.args.get("status", "")
    bulan_filter = request.args.get("bulan", "")
    seksi_filter = request.args.get("seksi", "")

    if not status_filter:
        status_filter = "Menunggu ACC"

    pengajuan = get_pengajuan_by_status(status_filter, bulan_filter, seksi_filter)
    semua_seksi = get_all_seksi()
    stats = get_stats()

    return render_template(
        "dashboard.html",
        pengajuan=pengajuan,
        semua_seksi=semua_seksi,
        bulan_filter=bulan_filter,
        seksi_filter=seksi_filter,
        status_filter=status_filter,
        stats=stats,
    )


@admin_bp.route("/detail/<string:pengajuan_id>")
@login_required
def detail(pengajuan_id):
    _require_valid_id(pengajuan_id)

    try:
        data = get_pengajuan_by_id(pengajuan_id)
    except Exception:
        data = None
    if not data:
        flash("Data tidak ditemukan.", "danger")
        return redirect(url_for("admin.dashboard"))

    return render_template("detail_pengajuan.html", data=data, pengajuan_id=pengajuan_id)


@admin_bp.route("/generate-surat/<string:pengajuan_id>")
@login_required
def generate_surat_route(pengajuan_id):
    _require_valid_id(pengajuan_id)

    try:
        data = get_pengajuan_by_id(pengajuan_id)
    except Exception:
        data = None
    if not data:
        flash("Data tidak ditemukan.", "danger")
        return redirect(url_for("admin.dashboard"))

    # Nomor surat dari form detail (diisi admin) — override data sheet
    no_surat_input = request.args.get("no_surat", "").strip()
    if no_surat_input:
        data["NO SURAT"] = no_surat_input

    try:
        docx_bytes = generate_surat(data)
        nama_file = str(data.get("NAMA", "unknown")).replace(" ", "_")
        filename = f"Surat_Cuti_{nama_file}.docx"

        # Simpan no_surat ke Sheets agar tidak hilang (Fix 2)
        if no_surat_input:
            try:
                from services.sheets_service import get_sheet
                sheet = get_sheet(SHEET_CUTI)
                headers = [h.strip() for h in sheet.row_values(1)]
                id_col = headers.index("ID") + 1
                id_values = sheet.col_values(id_col)
                try:
                    row_num = id_values.index(pengajuan_id) + 1
                    update_cell(SHEET_CUTI, row_num, "NO SURAT", no_surat_input)
                except ValueError:
                    pass  # Row tidak ditemukan, skip
            except Exception:
                pass  # Non-critical, jangan gagalkan generate surat

        return send_file(
            io.BytesIO(docx_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(safe_error_message(e, "generate surat"), "danger")
        return redirect(url_for("admin.dashboard"))


@admin_bp.route("/update-status/<string:pengajuan_id>", methods=["POST"])
@login_required
def update_status(pengajuan_id):
    validate_csrf()  # CSRF check
    _require_valid_id(pengajuan_id)

    status = request.form.get("status", "").strip()
    no_surat = request.form.get("no_surat", "").strip()

    if status not in ("Disetujui", "Ditolak", "Dibatalkan"):
        flash("Status tidak valid.", "danger")
        return redirect(url_for("admin.dashboard"))

    if status == "Disetujui" and not no_surat:
        flash("Nomor Surat wajib diisi untuk status Disetujui.", "danger")
        return redirect(url_for("admin.detail", pengajuan_id=pengajuan_id))

    # Validate no_surat format (alphanumeric + / only)
    if no_surat and not all(c.isalnum() or c in "/- .," for c in no_surat):
        flash("Format Nomor Surat tidak valid.", "danger")
        return redirect(url_for("admin.detail", pengajuan_id=pengajuan_id))

    try:
        update_status_by_id(SHEET_CUTI, pengajuan_id, status, no_surat or None)
        flash(f"Status berhasil diubah ke {status}.", "success")
    except Exception as e:
        flash(safe_error_message(e, "update status"), "danger")

    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/histori")
@login_required
def histori():
    semua = get_all_records(SHEET_CUTI)
    tahun_filter = request.args.get("tahun", str(get_tahun_sekarang()))
    bulan_filter = request.args.get("bulan", "")
    seksi_filter = request.args.get("seksi", "")
    status_filter = request.args.get("status", "")

    filtered = semua
    if tahun_filter:
        filtered = [r for r in filtered if str(r.get("TAHUN", "")) == tahun_filter]
    if bulan_filter:
        filtered = [r for r in filtered if bulan_filter.lower() in str(r.get("MASEHI", "")).lower()]
    if seksi_filter:
        filtered = [r for r in filtered if seksi_filter.lower() in str(r.get("SEKSI", "")).lower()]
    if status_filter:
        filtered = [r for r in filtered if r.get("STATUS", "").strip() == status_filter]

    semua_seksi = get_all_seksi()

    tahun = int(tahun_filter) if tahun_filter else get_tahun_sekarang()

    # Build indexes: sum hari kerja per NAMA for the target year
    # Kuota tahunan (exclude Sakit & Cuti Hamil) + Kuota hamil terpisah
    kuota_index = {}   # nama -> total hari kerja cuti tahunan
    hamil_index = {}   # nama -> total hari kerja cuti hamil
    nip_index = {}     # nama -> NI PPPK PW
    for r in semua:
        nama = str(r.get("NAMA", "")).strip()
        if not nama:
            continue
        if str(r.get("TAHUN", "")) != str(tahun):
            continue
        if r.get("STATUS", "").strip() != "Disetujui":
            continue

        keperluan = str(r.get("KEPERLUAN", "")).strip().upper()
        durasi = _get_durasi(r)

        if keperluan in ("CUTI HAMIL/MELAHIRKAN", "CUTI MELAHIRKAN"):
            hamil_index[nama] = hamil_index.get(nama, 0) + durasi
        elif keperluan != "SAKIT":
            kuota_index[nama] = kuota_index.get(nama, 0) + durasi

        # Track NI PPPK PW per nama (first seen wins)
        nip_val = str(r.get("NIP", "")).strip()
        if nip_val and nama not in nip_index:
            nip_index[nama] = nip_val

    karyawan_kuota = {}
    for nama in set(list(kuota_index.keys()) + list(hamil_index.keys())):
        terpakai = kuota_index.get(nama, 0)
        hamil_terpakai = hamil_index.get(nama, 0)
        nip = nip_index.get(nama, "-")
        entry = {
            "nip": nip,
            "nama": nama,
            "terpakai": terpakai,
            "sisa": max(KUOTA_TAHUNAN - terpakai, 0),
        }
        if hamil_terpakai > 0:
            entry["hamil_terpakai"] = hamil_terpakai
            entry["hamil_sisa"] = max(KUOTA_HAMIL - hamil_terpakai, 0)
        karyawan_kuota[nama] = entry

    return render_template(
        "histori.html",
        pengajuan=filtered,
        semua_seksi=semua_seksi,
        tahun_filter=tahun_filter,
        bulan_filter=bulan_filter,
        seksi_filter=seksi_filter,
        status_filter=status_filter,
        karyawan_kuota=karyawan_kuota,
    )


@admin_bp.route("/export-excel")
@login_required
def export_excel():
    dari_tahun = request.args.get("tahun", str(get_tahun_sekarang()))
    semua = get_all_records(SHEET_CUTI)
    filtered = [r for r in semua if str(r.get("TAHUN", "")) == dari_tahun]

    if not filtered:
        flash("Tidak ada data untuk diexport.", "warning")
        return redirect(url_for("admin.histori"))

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = f"Cuti {dari_tahun}"

    headers = [
        "NO", "MASEHI", "HARI", "NAMA", "KEPERLUAN", "NO SURAT",
        "JABATAN", "SEKSI", "SHIF", "KABID/KASI", "NIP", "STATUS",
        "TGL_SUBMIT", "TAHUN", "DURASI_HARI_KERJA", "CATATAN",
    ]
    ws.append(headers)

    for i, row in enumerate(filtered, 1):
        ws.append([
            i,
            row.get("MASEHI", ""),
            row.get("HARI", ""),
            row.get("NAMA", ""),
            row.get("KEPERLUAN", ""),
            row.get("NO SURAT", ""),
            row.get("JABATAN", ""),
            row.get("SEKSI", ""),
            row.get("SHIF", ""),
            row.get("KABID/KASI", ""),
            row.get("NIP", ""),
            row.get("STATUS", ""),
            row.get("TGL_SUBMIT", ""),
            row.get("TAHUN", ""),
            row.get("DURASI_HARI_KERJA", ""),
            row.get("CATATAN", ""),
        ])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"Rekap_Cuti_{dari_tahun}.xlsx"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@admin_bp.route("/hari-libur")
@login_required
def hari_libur():
    libur_list = get_all_hari_libur()
    # Sort by TANGGAL descending
    libur_list.sort(key=lambda x: str(x.get("TANGGAL", "")), reverse=True)
    return render_template("hari_libur.html", libur_list=libur_list)


@admin_bp.route("/hari-libur/add", methods=["POST"])
@login_required
def hari_libur_add():
    validate_csrf()
    tanggal = request.form.get("tanggal", "").strip()
    keterangan = request.form.get("keterangan", "").strip()

    if not tanggal or not keterangan:
        flash("Tanggal dan keterangan harus diisi.", "danger")
        return redirect(url_for("admin.hari_libur"))

    # format YYYY-MM-DD check
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", tanggal):
        flash("Format tanggal tidak valid.", "danger")
        return redirect(url_for("admin.hari_libur"))
        
    tahun = tanggal[:4]
    
    data = {
        "TANGGAL": tanggal,
        "KETERANGAN": keterangan,
        "TAHUN": tahun
    }
    
    try:
        append_row(SHEET_HARI_LIBUR, data)
        invalidate_hari_libur_cache()  # penting karena libur set punya cache sendiri
        flash("Hari libur berhasil ditambahkan.", "success")
    except Exception as e:
        flash(safe_error_message(e, "tambah hari libur"), "danger")

    return redirect(url_for("admin.hari_libur"))


@admin_bp.route("/hari-libur/delete/<string:tanggal>", methods=["POST"])
@login_required
def hari_libur_delete(tanggal):
    validate_csrf()
    try:
        delete_hari_libur_by_tanggal(tanggal)
        flash(f"Hari libur {tanggal} berhasil dihapus.", "success")
    except Exception as e:
        flash(safe_error_message(e, "hapus hari libur"), "danger")
        
    return redirect(url_for("admin.hari_libur"))
