import requests
from flask import Flask, render_template, request, redirect, url_for, session
import os
from login import login
from datetime import datetime, time, timedelta
import pytz
from log import *

app = Flask(__name__)
app.secret_key = os.urandom(24)

def format_date(date_str):
    try:
        return datetime.strptime(date_str, '%d-%m-%Y').strftime('%Y-%m-%d')
    except ValueError:
        return date_str

@app.route('/')
def index():
    if 'sessionid' not in session:
        return redirect(url_for('login_page'))

    # Create a new session request object
    session_req = requests.Session()

    # Set cookies and headers for the request
    session_req.cookies.set('sessionid', session['sessionid'])
    session_req.cookies.set('csrftoken', session['csrftoken_cookie'])

    # Get the lowongan data
    lowongan_data = get_accepted_lowongan(session_req, session['csrftoken_cookie'], session['sessionid'])

    # Filter latest period data
    latest_period = filter_by_latest_period_and_add_create_log(session_req, session['csrftoken_cookie'],
                                                               session['sessionid'], lowongan_data)

    # get combined logs
    combined_logs = get_combined_logs_for_latest_period(session_req, session['csrftoken_cookie'], session['sessionid'],
                                                        latest_period)

    # Convert logs to FullCalendar-compatible format
    formatted_logs = []
    for log in combined_logs:
        if log['Tanggal'] and log['Jam Mulai'] and log['Jam Selesai']:
            day, month, year = log['Tanggal'].split('-')
            formatted_date = f"{year}-{month}-{day}"  # Convert to 'YYYY-MM-DD' format

            formatted_logs.append({
                'title': log['Kategori'],
                'start': f"{formatted_date}T{log['Jam Mulai']}",
                'end': f"{formatted_date}T{log['Jam Selesai']}",
                'description': log['Deskripsi Tugas']
            })

    print(formatted_logs)

    return render_template('index.html', latest_period=latest_period, combined_logs=combined_logs, formatted_logs=formatted_logs)


@app.route('/log/<log_id>', methods=['GET'])
def view_log_per_lowongan(log_id):
    if 'sessionid' not in session:
        return redirect(url_for('login_page'))

    mata_kuliah = request.args.get('mata_kuliah')  # Ambil nama mata kuliah dari URL

    session_req = requests.Session()
    session_req.cookies.set('sessionid', session['sessionid'])
    session_req.cookies.set('csrftoken', session['csrftoken_cookie'])

    # Mengambil data log per lowongan menggunakan `get_log_per_lowongan`
    logs = get_log_per_lowongan(session_req, session['csrftoken_cookie'], session['sessionid'], log_id)

    # Berikan logs dan mata kuliah ke template
    return render_template('log_per_lowongan.html', logs=logs, mata_kuliah=mata_kuliah)


@app.route('/create-log/<create_log_id>', methods=['GET', 'POST'])
def create_log_view(create_log_id):
    if 'sessionid' not in session:
        return redirect(url_for('login_page'))

    error_message = None  # Variabel untuk menyimpan pesan error

    if request.method == 'POST':
        # Ambil data dari form
        kategori_log = request.form['kategori_log']
        deskripsi = request.form['deskripsi']
        tanggal = request.form['tanggal']  # format: 'YYYY-MM-DD'
        waktu_mulai = request.form['waktu_mulai']  # format: 'HH:MM'
        waktu_selesai = request.form['waktu_selesai']  # format: 'HH:MM'

        # Zona waktu WIB (UTC+7)
        wib = pytz.timezone('Asia/Jakarta')

        # Waktu saat ini dalam zona waktu WIB
        now_wib = datetime.now(wib)
        current_time_wib = now_wib.time()
        today_wib = now_wib.date()
        batas_waktu_wib = time(7, 0)  # 7:00 AM WIB

        try:
            # Parse tanggal dan waktu
            tanggal_parsed = datetime.strptime(tanggal, '%Y-%m-%d').date()
            waktu_mulai_parsed = datetime.strptime(waktu_mulai, '%H:%M').time()
            waktu_selesai_parsed = datetime.strptime(waktu_selesai, '%H:%M').time()
        except ValueError as e:
            error_message = "Format waktu atau tanggal tidak valid."
            return render_template('create_log.html', create_log_id=create_log_id, error_message=error_message)

        # Validasi: hanya izinkan menit yang bernilai 00, 15, 30, atau 45
        if waktu_mulai_parsed.minute not in [0, 15, 30, 45]:
            error_message = "Waktu mulai harus memiliki menit 00, 15, 30, atau 45!"
        elif waktu_selesai_parsed.minute not in [0, 15, 30, 45]:
            error_message = "Waktu selesai harus memiliki menit 00, 15, 30, atau 45!"
        # Validasi tanggal: pastikan saat ini sudah lebih dari pukul 7 WIB
        elif tanggal_parsed == today_wib and current_time_wib < batas_waktu_wib:
            error_message = "Anda belum bisa mengisi log untuk hari ini sebelum jam 7 pagi WIB!"
        elif tanggal_parsed > today_wib:
            error_message = "Tanggal tidak bisa di masa depan!"
        # Cek apakah waktu mulai < waktu selesai
        elif waktu_mulai_parsed >= waktu_selesai_parsed:
            error_message = "Waktu mulai harus lebih awal dari waktu selesai!"

        if error_message:
            return render_template('create_log.html', create_log_id=create_log_id, error_message=error_message)

        # Hitung durasi (dalam menit)
        waktu_mulai_combined = datetime.combine(tanggal_parsed, waktu_mulai_parsed)
        waktu_selesai_combined = datetime.combine(tanggal_parsed, waktu_selesai_parsed)
        durasi = (waktu_selesai_combined - waktu_mulai_combined).seconds // 60

        # Cek overlap dengan logs lain
        session_req = requests.Session()
        session_req.cookies.set('sessionid', session['sessionid'])
        session_req.cookies.set('csrftoken', session['csrftoken_cookie'])

        lowongan_data = get_accepted_lowongan(session_req, session['csrftoken_cookie'], session['sessionid'])
        latest_period = filter_by_latest_period_and_add_create_log(session_req, session['csrftoken_cookie'],
                                                                   session['sessionid'], lowongan_data)

        combined_logs = get_combined_logs_for_latest_period(session_req, session['csrftoken_cookie'],
                                                            session['sessionid'], latest_period)
        new_log = {
            'Tanggal': tanggal,
            'Jam Mulai': waktu_mulai,
            'Jam Selesai': waktu_selesai,
            'Durasi (Menit)': durasi,
            'Kategori': kategori_log,
            'Deskripsi Tugas': deskripsi,
        }
        combined_logs.append(new_log)

        overlap, overlap_logs = is_overlap(combined_logs)
        if overlap:
            # Formatkan pesan overlap menjadi lebih deskriptif
            overlap_message = "Log berikut menyebabkan overlap:<br>"
            for log1, log2 in overlap_logs:
                log1_course = log1.get('Mata Kuliah', 'Unknown Course')
                overlap_message += f"<strong>{log1_course}:</strong> {log1['Tanggal']} {log1['Jam Mulai']} - {log1['Jam Selesai']}<br><br>"

            return render_template('create_log.html', create_log_id=create_log_id, error_message=overlap_message)

        # Jika validasi lolos, lakukan POST untuk membuat log
        create_log(session_req, session['csrftoken_cookie'], session['sessionid'], session['csrf_token'],
                   create_log_id, kategori_log, deskripsi,
                   {'day': tanggal_parsed.day, 'month': tanggal_parsed.month, 'year': tanggal_parsed.year},
                   {'hour': waktu_mulai_parsed.hour, 'minute': waktu_mulai_parsed.minute},
                   {'hour': waktu_selesai_parsed.hour, 'minute': waktu_selesai_parsed.minute})

        return redirect(url_for('index'))

    return render_template('create_log.html', create_log_id=create_log_id)


@app.route('/edit-log/<log_id>', methods=['GET', 'POST'])
def edit_log_view(log_id):
    # Cek apakah user sudah login
    if 'sessionid' not in session:
        return redirect(url_for('login_page'))

    error_message = None  # Variabel untuk menyimpan pesan error

    session_req = requests.Session()
    session_req.cookies.set('sessionid', session['sessionid'])
    session_req.cookies.set('csrftoken', session['csrftoken_cookie'])

    # Dapatkan logs dari combined_logs
    lowongan_data = get_accepted_lowongan(session_req, session['csrftoken_cookie'], session['sessionid'])
    latest_period = filter_by_latest_period_and_add_create_log(session_req, session['csrftoken_cookie'],
                                                               session['sessionid'], lowongan_data)
    combined_logs = get_combined_logs_for_latest_period(session_req, session['csrftoken_cookie'],
                                                        session['sessionid'], latest_period)

    # Cari log berdasarkan log_id dari combined_logs
    log_to_edit = next((log for log in combined_logs if log.get('LogID') == log_id), None)

    if not log_to_edit:
        return f"Log with ID {log_id} not found", 404

    try:
        tanggal_obj = datetime.strptime(log_to_edit['Tanggal'], '%d-%m-%Y')
        log_to_edit['Tanggal'] = tanggal_obj.strftime(
            '%Y-%m-%d')  # Konversi ke 'yyyy-MM-dd' untuk HTML input[type="date"]
    except ValueError:
        error_message = "Format tanggal pada log tidak valid."
        return render_template('edit_log.html', log_id=log_id, log=log_to_edit, error_message=error_message)

    # Jika POST, lakukan update
    if request.method == 'POST':
        # Ambil data dari form
        kategori_log = request.form['kategori_log']
        deskripsi = request.form['deskripsi']
        tanggal = request.form['tanggal']  # format: 'YYYY-MM-DD'
        waktu_mulai = request.form['waktu_mulai']  # format: 'HH:MM'
        waktu_selesai = request.form['waktu_selesai']  # format: 'HH:MM'

        # Zona waktu WIB (UTC+7)
        wib = pytz.timezone('Asia/Jakarta')

        # Waktu saat ini dalam zona waktu WIB
        now_wib = datetime.now(wib)
        current_time_wib = now_wib.time()
        today_wib = now_wib.date()
        batas_waktu_wib = time(7, 0)  # 7:00 AM WIB

        try:
            # Parse tanggal dan waktu
            tanggal_parsed = datetime.strptime(tanggal, '%Y-%m-%d').date()
            waktu_mulai_parsed = datetime.strptime(waktu_mulai, '%H:%M').time()
            waktu_selesai_parsed = datetime.strptime(waktu_selesai, '%H:%M').time()
        except ValueError as e:
            error_message = "Format waktu atau tanggal tidak valid."
            return render_template('edit_log.html', log_id=log_id, error_message=error_message, log=log_to_edit)

        # Validasi: hanya izinkan menit yang bernilai 00, 15, 30, atau 45
        if waktu_mulai_parsed.minute not in [0, 15, 30, 45]:
            error_message = "Waktu mulai harus memiliki menit 00, 15, 30, atau 45!"
        elif waktu_selesai_parsed.minute not in [0, 15, 30, 45]:
            error_message = "Waktu selesai harus memiliki menit 00, 15, 30, atau 45!"
        # Validasi tanggal: pastikan saat ini sudah lebih dari pukul 7 WIB
        elif tanggal_parsed == today_wib and current_time_wib < batas_waktu_wib:
            error_message = "Anda belum bisa mengisi log untuk hari ini sebelum jam 7 pagi WIB!"
        elif tanggal_parsed > today_wib:
            error_message = "Tanggal tidak bisa di masa depan!"
        # Cek apakah waktu mulai < waktu selesai
        elif waktu_mulai_parsed >= waktu_selesai_parsed:
            error_message = "Waktu mulai harus lebih awal dari waktu selesai!"

        if error_message:
            return render_template('edit_log.html', log_id=log_id, error_message=error_message, log=log_to_edit)

        # Hitung durasi (dalam menit)
        waktu_mulai_combined = datetime.combine(tanggal_parsed, waktu_mulai_parsed)
        waktu_selesai_combined = datetime.combine(tanggal_parsed, waktu_selesai_parsed)
        durasi = (waktu_selesai_combined - waktu_mulai_combined).seconds // 60

        # Cek overlap dengan logs lain
        session_req = requests.Session()
        session_req.cookies.set('sessionid', session['sessionid'])
        session_req.cookies.set('csrftoken', session['csrftoken_cookie'])

        lowongan_data = get_accepted_lowongan(session_req, session['csrftoken_cookie'], session['sessionid'])
        latest_period = filter_by_latest_period_and_add_create_log(session_req, session['csrftoken_cookie'],
                                                                   session['sessionid'], lowongan_data)

        combined_logs = get_combined_logs_for_latest_period(session_req, session['csrftoken_cookie'],
                                                            session['sessionid'], latest_period)

        # Hapus log yang sedang di-edit dari daftar logs
        combined_logs = [log for log in combined_logs if log.get('LogID') != log_id]

        # Buat log yang sudah diedit
        edited_log = {
            'Tanggal': tanggal,
            'Jam Mulai': waktu_mulai,
            'Jam Selesai': waktu_selesai,
            'Durasi (Menit)': durasi,
            'Kategori': kategori_log,
            'Deskripsi Tugas': deskripsi,
        }
        combined_logs.append(edited_log)

        # Cek apakah ada overlap
        overlap, overlap_logs = is_overlap(combined_logs)
        if overlap:
            # Formatkan pesan overlap menjadi lebih deskriptif
            overlap_message = "Log berikut menyebabkan overlap:<br>"
            for log1, log2 in overlap_logs:
                log1_course = log1.get('Mata Kuliah', 'Unknown Course')
                overlap_message += f"<strong>{log1_course}:</strong> {log1['Tanggal']} {log1['Jam Mulai']} - {log1['Jam Selesai']}<br><br>"
            return render_template('edit_log.html', log_id=log_id, error_message=overlap_message, log=log_to_edit)

        # Update log
        update_log(
            session=session_req,
            csrftoken_cookie=session['csrftoken_cookie'],
            sessionid=session['sessionid'],
            csrfmiddlewaretoken=session['csrf_token'],
            log_id=log_id,
            kategori_log=kategori_log,
            deskripsi=deskripsi,
            tanggal={'day': tanggal_parsed.day, 'month': tanggal_parsed.month, 'year': tanggal_parsed.year},
            waktu_mulai={'hour': waktu_mulai_parsed.hour, 'minute': waktu_mulai_parsed.minute},
            waktu_selesai={'hour': waktu_selesai_parsed.hour, 'minute': waktu_selesai_parsed.minute}
        )

        return redirect(url_for('index'))

    # Jika GET, render form dengan data prefilled dari log_to_edit
    return render_template('edit_log.html', log_id=log_id, log=log_to_edit)



@app.route('/delete-log/<log_id>', methods=['POST'])
def delete_log_view(log_id):
    # Cek apakah user sudah login
    if 'sessionid' not in session:
        return redirect(url_for('login_page'))

    session_req = requests.Session()

    # Proses delete log menggunakan fungsi `delete_log`
    delete_log(
        session=session_req,
        csrftoken_cookie=session['csrftoken_cookie'],
        sessionid=session['sessionid'],
        csrfmiddlewaretoken=session['csrf_token'],
        log_id=log_id
    )

    return redirect(url_for('index'))


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    error_message = None  # Inisialisasi pesan error

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Create a session
        session_req = requests.Session()

        # Call the login function
        csrf_token, csrftoken_cookie, sessionid = login(session_req, username, password)

        if sessionid:
            session['username'] = username
            session['csrf_token'] = csrf_token
            session['sessionid'] = sessionid
            session['csrftoken_cookie'] = csrftoken_cookie
            return redirect(url_for('index'))
        else:
            error_message = "Login gagal, silakan periksa username atau password Anda."

    return render_template('login.html', error_message=error_message)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
