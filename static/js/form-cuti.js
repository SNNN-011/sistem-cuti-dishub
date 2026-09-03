// ===== NI PPPK PW validation =====
document.getElementById('ni_pppk_pw').addEventListener('blur', async function() {
    const niPppkPw = this.value.trim();
    const status = document.getElementById('ni-pppk-pw-status');
    if (!niPppkPw) { status.textContent = ''; return; }
    status.textContent = 'Mencari...';
    status.className = 'text-xs mt-0.5 block text-base-content/50';
    try {
        const resp = await fetch(`/api/karyawan/validate/${niPppkPw}`);
        const data = await resp.json();
        const span = document.createElement('span');
        if (data.valid) {
            span.className = 'text-success font-medium';
            span.textContent = '✓ NI PPPK PW terdaftar';
        } else {
            span.className = 'text-error font-medium';
            span.textContent = '✗ NI PPPK PW tidak terdaftar';
        }
        status.replaceChildren(span);
    } catch(e) { status.textContent = ''; }
});

// ===== Flatpickr + Cuti Melahirkan logic =====
document.addEventListener('DOMContentLoaded', function() {
    if (typeof flatpickr === 'undefined') return;

    if (flatpickr.l10ns && flatpickr.l10ns.id) {
        flatpickr.localize(flatpickr.l10ns.id);
    }

    const commonConfig = {
        altInput: true,
        altFormat: "d/m/Y",
        dateFormat: "Y-m-d",
        allowInput: true,
        altInputClass: "input w-full text-sm"
    };

    // Elemen DOM
    const keperluanSelect = document.getElementById("keperluan");
    const tglMulaiInput = document.getElementById("tgl_mulai");
    const wrapperFlatpickr = document.getElementById("wrapper-flatpickr-selesai");
    const wrapperReadonly = document.getElementById("wrapper-readonly-selesai");
    const inputReadonly = document.getElementById("tgl_selesai_readonly");
    const inputTglSelesai = document.getElementById("tgl_selesai");

    let fpSelesai = null;

    const isMelahirkan = () => {
        const v = (keperluanSelect.value || "").toUpperCase();
        return v === "CUTI MELAHIRKAN" || v === "CUTI HAMIL/MELAHIRKAN";
    };

    // Ambil tanggal mulai dalam format YYYY-MM-DD
    const getTglMulaiISO = () => {
        if (tglMulaiInput.value) return tglMulaiInput.value;
        if (tglMulaiInput._flatpickr && tglMulaiInput._flatpickr.selectedDates.length > 0) {
            return tglMulaiInput._flatpickr.formatDate(tglMulaiInput._flatpickr.selectedDates[0], "Y-m-d");
        }
        return "";
    };

    // Buat/set hidden input untuk nilai tgl_selesai hasil API
    const setHiddenSelesai = (value) => {
        let hidden = document.getElementById("tgl_selesai_hidden");
        if (!hidden) {
            hidden = document.createElement("input");
            hidden.type = "hidden";
            hidden.id = "tgl_selesai_hidden";
            hidden.name = "tgl_selesai";
            wrapperReadonly.appendChild(hidden);
        }
        hidden.value = value || "";
    };

    const removeHiddenSelesai = () => {
        const hidden = document.getElementById("tgl_selesai_hidden");
        if (hidden) hidden.remove();
    };

    // Fetch /api/hitung-90-hari-kerja
    async function fetchSelesaiAndSetAPI() {
        const mulai = getTglMulaiISO();
        if (mulai) {
            inputReadonly.value = "Menghitung...";
            try {
                const resp = await fetch(`/api/hitung-90-hari-kerja?mulai=${encodeURIComponent(mulai)}`);
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                const data = await resp.json();
                if (data.selesai) {
                    inputReadonly.value = data.selesai;
                    setHiddenSelesai(data.selesai);
                } else {
                    inputReadonly.value = "Gagal menghitung";
                    setHiddenSelesai("");
                }
            } catch(err) {
                console.error("Gagal hitung tanggal selesai:", err);
                inputReadonly.value = "Gagal menghitung";
                setHiddenSelesai("");
            }
        } else {
            inputReadonly.value = "";
            setHiddenSelesai("");
        }
    }

    // Inisialisasi flatpickr Tanggal Selesai (hanya untuk mode non-melahirkan)
    function initSelesai() {
        if (fpSelesai) {
            fpSelesai.destroy();
            fpSelesai = null;
        }

        let minDateSelesai = "today";
        const currentTglMulai = tglMulaiInput._flatpickr;
        if (currentTglMulai && currentTglMulai.selectedDates.length > 0) {
            minDateSelesai = currentTglMulai.selectedDates[0];
        }

        fpSelesai = flatpickr(inputTglSelesai, Object.assign({}, commonConfig, {
            minDate: minDateSelesai
        }));
    }

    // Mode Cuti Melahirkan: sembunyikan flatpickr, tampilkan field otomatis
    function activateMelahirkan() {
        // Sembunyikan flatpickr total
        wrapperFlatpickr.style.display = "none";
        if (fpSelesai) {
            fpSelesai.destroy();
            fpSelesai = null;
        }
        inputTglSelesai.disabled = true;
        inputTglSelesai.removeAttribute("required");

        // Tampilkan field readonly otomatis
        wrapperReadonly.style.display = "block";

        // Langsung hitung jika tanggal mulai sudah terisi
        fetchSelesaiAndSetAPI();
    }

    // Mode normal: tampilkan flatpickr, sembunyikan field otomatis
    function deactivateMelahirkan() {
        wrapperReadonly.style.display = "none";
        removeHiddenSelesai();

        inputTglSelesai.disabled = false;
        inputTglSelesai.required = true;

        wrapperFlatpickr.style.display = "block";
        initSelesai();
    }

    function handleKeperluanChange() {
        if (isMelahirkan()) {
            activateMelahirkan();
        } else {
            deactivateMelahirkan();
        }
    }

    // Event: dropdown keperluan berubah
    keperluanSelect.addEventListener("change", handleKeperluanChange);

    // Init flatpickr Tanggal Mulai
    flatpickr("#tgl_mulai", Object.assign({}, commonConfig, {
        minDate: "today",
        onChange: function(selectedDates, dateStr) {
            // Update minDate flatpickr selesai jika ada
            if (selectedDates.length > 0 && fpSelesai) {
                fpSelesai.set("minDate", selectedDates[0]);
                if (fpSelesai.selectedDates.length > 0 && fpSelesai.selectedDates[0] < selectedDates[0]) {
                    fpSelesai.setDate(selectedDates[0]);
                }
            }
            // Jika Cuti Melahirkan aktif, hitung ulang otomatis
            if (isMelahirkan()) {
                fetchSelesaiAndSetAPI();
            }
        }
    }));

    // Jaga-jaga: jika user mengetik tanggal mulai manual (allowInput), tetap trigger
    tglMulaiInput.addEventListener("change", function() {
        if (isMelahirkan()) {
            fetchSelesaiAndSetAPI();
        }
    });

    // Panggil sekali saat load (restore state dari server-side form_data)
    handleKeperluanChange();
});

// ===== Validasi submit =====
document.getElementById('formCuti').addEventListener('submit', function(e) {
    const tgl_mulai = document.getElementById('tgl_mulai').value;
    const tgl_selesai = document.getElementById('tgl_selesai').value;
    if (tgl_selesai && tgl_mulai && tgl_selesai < tgl_mulai) {
        e.preventDefault();
        alert('Tanggal selesai tidak boleh sebelum tanggal mulai.');
    }
});
