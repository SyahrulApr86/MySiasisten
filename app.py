from flask import Flask, render_template, request, redirect, url_for, session
import pytz
from datetime import time
from flask import jsonify
from flask_caching import Cache
from celery import Celery
from log import *


def make_celery(app):
    celery = Celery(app.import_name, backend=app.config['CELERY_RESULT_BACKEND'],
                    broker=app.config['CELERY_BROKER_URL'])
    celery.conf.update(app.config)
    return celery


app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['CACHE_TYPE'] = 'RedisCache'
app.config['CACHE_REDIS_HOST'] = 'redis'
app.config['CACHE_REDIS_PORT'] = 6379
app.config['CACHE_REDIS_DB'] = 0
app.config['CACHE_REDIS_URL'] = 'redis://redis:6379/0'
app.config['CACHE_DEFAULT_TIMEOUT'] = 300

cache = Cache(app)

app.config.update(
    CELERY_BROKER_URL='redis://redis:6379/0',
    CELERY_RESULT_BACKEND='redis://redis:6379/0'
)

celery = make_celery(app)


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
    session_req.cookies.set('sessionid', session['sessionid'])
    session_req.cookies.set('csrftoken', session['csrftoken_cookie'])

    cache_key_lowongan = f'lowongan_data_{session["sessionid"]}'
    cache_key_latest_period = f'latest_period_{session["sessionid"]}'
    cache_key_combined_logs = f'combined_logs_{session["sessionid"]}'
    cache_key_formatted_logs = f'formatted_logs_{session["sessionid"]}'

    # Cek cache untuk `lowongan_data`
    lowongan_data = cache.get(cache_key_lowongan)
    if lowongan_data is None:
        lowongan_data = get_accepted_lowongan(session_req, session['csrftoken_cookie'], session['sessionid'])
        cache.set(cache_key_lowongan, lowongan_data, timeout=3600 * 12)  # Cache selama 12 jam

    # Cek cache untuk `latest_period`
    latest_period = cache.get(cache_key_latest_period)
    if latest_period is None:
        latest_period = filter_by_latest_period_and_add_create_log(session_req, session['csrftoken_cookie'],
                                                                   session['sessionid'], lowongan_data)
        cache.set(cache_key_latest_period, latest_period, timeout=60)  # Cache selama 1 menit

    # Cek cache untuk `combined_logs`
    combined_logs = cache.get(cache_key_combined_logs)
    if combined_logs is None:
        combined_logs = get_combined_logs_for_latest_period_parallel(session_req, session['csrftoken_cookie'],
                                                                     session['sessionid'], latest_period)
        cache.set(cache_key_combined_logs, combined_logs, timeout=60)  # Cache selama 1 menit

    # Cek cache untuk `formatted_logs`
    formatted_logs = cache.get(cache_key_formatted_logs)
    if formatted_logs is None:
        formatted_logs = []
        for log in combined_logs:
            if log['Tanggal'] and log['Jam Mulai'] and log['Jam Selesai']:
                day, month, year = log['Tanggal'].split('-')
                formatted_date = f"{year}-{month}-{day}"  # Convert to 'YYYY-MM-DD' format

                formatted_logs.append({
                    'title': log['Kategori'],
                    'start': f"{formatted_date}T{log['Jam Mulai']}",
                    'end': f"{formatted_date}T{log['Jam Selesai']}",
                    'description': log['Deskripsi Tugas'],
                    'duration': str(log['Durasi (Menit)']),
                    'kategori': log['Kategori'],
                    'status': log['Status'],
                    'edit_url': url_for('edit_log_view', log_id=log['LogID']),
                    'course': log.get('Mata Kuliah', 'Unknown Course')
                })
        cache.set(cache_key_formatted_logs, formatted_logs, timeout=0)

    return render_template('index.html', latest_period=latest_period, combined_logs=combined_logs,
                           formatted_logs=formatted_logs)


@app.route('/log/<log_id>', methods=['GET'])
def view_log_per_lowongan(log_id):
    if 'sessionid' not in session:
        return redirect(url_for('login_page'))

    mata_kuliah = request.args.get('mata_kuliah')  # Ambil nama mata kuliah dari URL

    session_req = requests.Session()
    session_req.cookies.set('sessionid', session['sessionid'])
    session_req.cookies.set('csrftoken', session['csrftoken_cookie'])

    # Buat cache key berdasarkan log_id dan mata_kuliah untuk membedakan cache setiap request
    cache_key = f'log_per_lowongan_{log_id}_{mata_kuliah}'

    # Cek apakah data sudah ada di cache
    cached_logs = cache.get(cache_key)
    if cached_logs is None:
        # Jika tidak ada di cache, ambil data log per lowongan menggunakan `get_log_per_lowongan`
        logs = get_log_per_lowongan(session_req, session['csrftoken_cookie'], session['sessionid'], log_id)

        # Simpan data logs ke dalam cache dengan timeout 1 menit (60 detik)
        cache.set(cache_key, logs, timeout=60)
    else:
        # Jika ada di cache, gunakan data yang sudah di-cache
        logs = cached_logs

    # Berikan logs dan mata kuliah ke template
    return render_template('log_per_lowongan.html', logs=logs, mata_kuliah=mata_kuliah)


@celery.task(name="app.create_log_async")
def create_log_async(session_data, log_data):
    session_req = requests.Session()
    session_req.cookies.set('sessionid', session_data['sessionid'])
    session_req.cookies.set('csrftoken', session_data['csrftoken_cookie'])

    # Lakukan pembuatan log baru
    create_log(
        session=session_req,
        csrftoken_cookie=session_data['csrftoken_cookie'],
        sessionid=session_data['sessionid'],
        csrfmiddlewaretoken=session_data['csrf_token'],
        create_log_id=log_data['create_log_id'],
        kategori_log=log_data['kategori_log'],
        deskripsi=log_data['deskripsi'],
        tanggal=log_data['tanggal'],
        waktu_mulai=log_data['waktu_mulai'],
        waktu_selesai=log_data['waktu_selesai']
    )

    # Hapus cache setelah membuat log baru
    cache.delete(log_data['cache_key_combined_logs'])
    cache.delete(log_data['cache_key_formatted_logs'])

    update_data()


@app.route('/create-log/<create_log_id>', methods=['GET', 'POST'])
def create_log_view(create_log_id):
    if 'sessionid' not in session:
        return redirect(url_for('login_page'))

    error_message = None

    session_req = requests.Session()
    session_req.cookies.set('sessionid', session['sessionid'])
    session_req.cookies.set('csrftoken', session['csrftoken_cookie'])

    # Buat cache key berdasarkan session ID
    cache_key_lowongan = f'lowongan_data_{session["sessionid"]}'
    cache_key_latest_period = f'latest_period_{session["sessionid"]}'
    cache_key_combined_logs = f'combined_logs_{session["sessionid"]}'
    cache_key_formatted_logs = f'formatted_logs_{session["sessionid"]}'

    # Cek cache untuk `lowongan_data`
    lowongan_data = cache.get(cache_key_lowongan)
    if lowongan_data is None:
        lowongan_data = get_accepted_lowongan(session_req, session['csrftoken_cookie'], session['sessionid'])
        cache.set(cache_key_lowongan, lowongan_data, timeout=3600 * 12)  # Cache selama 12 jam

    # Cek cache untuk `latest_period`
    latest_period = cache.get(cache_key_latest_period)
    if latest_period is None:
        latest_period = filter_by_latest_period_and_add_create_log(session_req, session['csrftoken_cookie'],
                                                                   session['sessionid'], lowongan_data)
        cache.set(cache_key_latest_period, latest_period, timeout=60)  # Cache selama 1 menit

    # Cek cache untuk `combined_logs`
    combined_logs = cache.get(cache_key_combined_logs)
    if combined_logs is None:
        combined_logs = get_combined_logs_for_latest_period(session_req, session['csrftoken_cookie'],
                                                            session['sessionid'], latest_period)
        cache.set(cache_key_combined_logs, combined_logs, timeout=60)  # Cache selama 1 menit

    # Cek cache untuk `formatted_logs`
    formatted_logs = cache.get(cache_key_formatted_logs)
    if formatted_logs is None:
        formatted_logs = []
        for log in combined_logs:
            if log['Tanggal'] and log['Jam Mulai'] and log['Jam Selesai']:
                day, month, year = log['Tanggal'].split('-')
                formatted_date = f"{year}-{month}-{day}"

                formatted_logs.append({
                    'title': log['Kategori'],
                    'start': f"{formatted_date}T{log['Jam Mulai']}",
                    'end': f"{formatted_date}T{log['Jam Selesai']}",
                    'description': log['Deskripsi Tugas'],
                    'duration': str(log['Durasi (Menit)']),
                    'kategori': log['Kategori'],
                    'status': log['Status'],
                    'edit_url': url_for('edit_log_view', log_id=log['LogID']),
                    'course': log.get('Mata Kuliah', 'Unknown Course')
                })
        cache.set(cache_key_formatted_logs, formatted_logs, timeout=60)  # Cache selama 1 menit

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
        except ValueError:
            error_message = "Format waktu atau tanggal tidak valid."
            return render_template('create_log.html',
                                   create_log_id=create_log_id,
                                   error_message=error_message,
                                   formatted_logs=formatted_logs)

        # Validasi: hanya izinkan menit yang bernilai 00, 15, 30, atau 45
        if waktu_mulai_parsed.minute not in [0, 15, 30, 45]:
            error_message = "Waktu mulai harus memiliki menit 00, 15, 30, atau 45!"
        elif waktu_selesai_parsed.minute not in [0, 15, 30, 45]:
            error_message = "Waktu selesai harus memiliki menit 00, 15, 30, atau 45!"
        elif tanggal_parsed == today_wib and current_time_wib < batas_waktu_wib:
            error_message = "Anda belum bisa mengisi log untuk hari ini sebelum jam 7 pagi WIB!"
        elif tanggal_parsed > today_wib:
            error_message = "Tanggal tidak bisa di masa depan!"
        elif waktu_mulai_parsed >= waktu_selesai_parsed:
            error_message = "Waktu mulai harus lebih awal dari waktu selesai!"

        if error_message:
            return render_template('create_log.html',
                                   create_log_id=create_log_id,
                                   error_message=error_message,
                                   formatted_logs=formatted_logs)

        # Hitung durasi (dalam menit)
        waktu_mulai_combined = datetime.combine(tanggal_parsed, waktu_mulai_parsed)
        waktu_selesai_combined = datetime.combine(tanggal_parsed, waktu_selesai_parsed)
        durasi = (waktu_selesai_combined - waktu_mulai_combined).seconds // 60

        # Tambahkan log baru
        new_log = {
            'Tanggal': tanggal,
            'Jam Mulai': waktu_mulai,
            'Jam Selesai': waktu_selesai,
            'Durasi (Menit)': durasi,
            'Kategori': kategori_log,
            'Deskripsi Tugas': deskripsi,
        }
        combined_logs.append(new_log)

        # Cek overlap dengan logs lain
        overlap, overlap_logs = is_overlap(combined_logs)
        if overlap:
            overlap_message = "Log berikut menyebabkan overlap:<br>"
            for log1, log2 in overlap_logs:
                log1_course = log1.get('Mata Kuliah', 'Unknown Course')
                overlap_message += f"<strong>{log1_course}:</strong> {log1['Tanggal']} {log1['Jam Mulai']} - {log1['Jam Selesai']}<br><br>"
            return render_template('create_log.html',
                                   create_log_id=create_log_id,
                                   error_message=overlap_message,
                                   formatted_logs=formatted_logs)

        # Persiapkan data untuk dikirim ke tugas asinkron
        log_data = {
            'create_log_id': create_log_id,
            'kategori_log': kategori_log,
            'deskripsi': deskripsi,
            'tanggal': {'day': tanggal_parsed.day, 'month': tanggal_parsed.month, 'year': tanggal_parsed.year},
            'waktu_mulai': {'hour': waktu_mulai_parsed.hour, 'minute': waktu_mulai_parsed.minute},
            'waktu_selesai': {'hour': waktu_selesai_parsed.hour, 'minute': waktu_selesai_parsed.minute},
            'cache_key_combined_logs': cache_key_combined_logs,
            'cache_key_formatted_logs': cache_key_formatted_logs
        }

        session_data = {
            'sessionid': session['sessionid'],
            'csrftoken_cookie': session['csrftoken_cookie'],
            'csrf_token': session['csrf_token']
        }

        # Panggil tugas asinkron untuk membuat log baru
        create_log_async.delay(session_data, log_data)

        return redirect(url_for('index'))

    return render_template('create_log.html',
                           create_log_id=create_log_id,
                           error_message=error_message,
                           formatted_logs=formatted_logs)


@celery.task(name="app.update_log_async")
def update_log_async(session_data, log_data):
    session_req = requests.Session()
    session_req.cookies.set('sessionid', session_data['sessionid'])
    session_req.cookies.set('csrftoken', session_data['csrftoken_cookie'])

    # Lakukan update log
    update_log(
        session=session_req,
        csrftoken_cookie=session_data['csrftoken_cookie'],
        sessionid=session_data['sessionid'],
        csrfmiddlewaretoken=session_data['csrf_token'],
        log_id=log_data['log_id'],
        kategori_log=log_data['kategori_log'],
        deskripsi=log_data['deskripsi'],
        tanggal=log_data['tanggal'],
        waktu_mulai=log_data['waktu_mulai'],
        waktu_selesai=log_data['waktu_selesai']
    )

    # Hapus cache setelah update
    cache.delete(log_data['cache_key_combined_logs'])
    cache.delete(log_data['cache_key_formatted_logs'])

    update_data()


@app.route('/edit-log/<log_id>', methods=['GET', 'POST'])
def edit_log_view(log_id):
    if 'sessionid' not in session:
        return redirect(url_for('login_page'))

    error_message = None

    session_req = requests.Session()
    session_req.cookies.set('sessionid', session['sessionid'])
    session_req.cookies.set('csrftoken', session['csrftoken_cookie'])

    # Buat cache key berdasarkan session ID
    cache_key_lowongan = f'lowongan_data_{session["sessionid"]}'
    cache_key_latest_period = f'latest_period_{session["sessionid"]}'
    cache_key_combined_logs = f'combined_logs_{session["sessionid"]}'
    cache_key_formatted_logs = f'formatted_logs_{session["sessionid"]}'

    # Cek cache untuk `lowongan_data`
    lowongan_data = cache.get(cache_key_lowongan)
    if lowongan_data is None:
        lowongan_data = get_accepted_lowongan(session_req, session['csrftoken_cookie'], session['sessionid'])
        cache.set(cache_key_lowongan, lowongan_data, timeout=3600 * 12)  # Cache selama 12 jam

    # Cek cache untuk `latest_period`
    latest_period = cache.get(cache_key_latest_period)
    if latest_period is None:
        latest_period = filter_by_latest_period_and_add_create_log(session_req, session['csrftoken_cookie'],
                                                                   session['sessionid'], lowongan_data)
        cache.set(cache_key_latest_period, latest_period, timeout=60)  # Cache selama 1 menit

    # Cek cache untuk `combined_logs`
    combined_logs = cache.get(cache_key_combined_logs)
    if combined_logs is None:
        combined_logs = get_combined_logs_for_latest_period(session_req, session['csrftoken_cookie'],
                                                            session['sessionid'], latest_period)
        cache.set(cache_key_combined_logs, combined_logs, timeout=60)  # Cache selama 1 menit

    # Cek cache untuk `formatted_logs`
    formatted_logs = cache.get(cache_key_formatted_logs)
    if formatted_logs is None:
        formatted_logs = []
        for log in combined_logs:
            if log['Tanggal'] and log['Jam Mulai'] and log['Jam Selesai']:
                day, month, year = log['Tanggal'].split('-')
                formatted_date = f"{year}-{month}-{day}"

                formatted_logs.append({
                    'title': log['Kategori'],
                    'start': f"{formatted_date}T{log['Jam Mulai']}",
                    'end': f"{formatted_date}T{log['Jam Selesai']}",
                    'description': log['Deskripsi Tugas'],
                    'duration': str(log['Durasi (Menit)']),
                    'kategori': log['Kategori'],
                    'status': log['Status'],
                    'edit_url': url_for('edit_log_view', log_id=log['LogID']),
                    'course': log.get('Mata Kuliah', 'Unknown Course')
                })
        cache.set(cache_key_formatted_logs, formatted_logs, timeout=60)  # Cache selama 1 menit

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
        return render_template('edit_log.html',
                               log_id=log_id,
                               log=log_to_edit,
                               error_message=error_message,
                               formatted_logs=formatted_logs)

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
        now_wib = datetime.now(wib)
        current_time_wib = now_wib.time()
        today_wib = now_wib.date()
        batas_waktu_wib = time(7, 0)  # 7:00 AM WIB

        try:
            tanggal_parsed = datetime.strptime(tanggal, '%Y-%m-%d').date()
            waktu_mulai_parsed = datetime.strptime(waktu_mulai, '%H:%M').time()
            waktu_selesai_parsed = datetime.strptime(waktu_selesai, '%H:%M').time()
        except ValueError:
            error_message = "Format waktu atau tanggal tidak valid."
            return render_template('edit_log.html',
                                   log_id=log_id,
                                   error_message=error_message,
                                   log=log_to_edit,
                                   formatted_logs=formatted_logs)

        # Validasi waktu
        if waktu_mulai_parsed.minute not in [0, 15, 30, 45]:
            error_message = "Waktu mulai harus memiliki menit 00, 15, 30, atau 45!"
        elif waktu_selesai_parsed.minute not in [0, 15, 30, 45]:
            error_message = "Waktu selesai harus memiliki menit 00, 15, 30, atau 45!"
        elif tanggal_parsed == today_wib and current_time_wib < batas_waktu_wib:
            error_message = "Anda belum bisa mengisi log untuk hari ini sebelum jam 7 pagi WIB!"
        elif tanggal_parsed > today_wib:
            error_message = "Tanggal tidak bisa di masa depan!"
        elif waktu_mulai_parsed >= waktu_selesai_parsed:
            error_message = "Waktu mulai harus lebih awal dari waktu selesai!"

        if error_message:
            return render_template('edit_log.html',
                                   log_id=log_id,
                                   error_message=error_message,
                                   log=log_to_edit,
                                   formatted_logs=formatted_logs)

        # Hitung durasi (dalam menit)
        waktu_mulai_combined = datetime.combine(tanggal_parsed, waktu_mulai_parsed)
        waktu_selesai_combined = datetime.combine(tanggal_parsed, waktu_selesai_parsed)
        durasi = (waktu_selesai_combined - waktu_mulai_combined).seconds // 60

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
            'LogID': log_id
        }
        combined_logs.append(edited_log)

        # Cek apakah ada overlap
        overlap, overlap_logs = is_overlap(combined_logs)
        if overlap:
            overlap_message = "Log berikut menyebabkan overlap:<br>"
            for log1, log2 in overlap_logs:
                log1_course = log1.get('Mata Kuliah', 'Unknown Course')
                overlap_message += f"<strong>{log1_course}:</strong> {log1['Tanggal']} {log1['Jam Mulai']} - {log1['Jam Selesai']}<br><br>"
            return render_template('edit_log.html',
                                   log_id=log_id,
                                   error_message=overlap_message,
                                   log=log_to_edit,
                                   formatted_logs=formatted_logs)

        # Persiapkan data untuk dikirim ke tugas asinkron
        log_data = {
            'log_id': log_id,
            'kategori_log': kategori_log,
            'deskripsi': deskripsi,
            'tanggal': {'day': tanggal_parsed.day, 'month': tanggal_parsed.month, 'year': tanggal_parsed.year},
            'waktu_mulai': {'hour': waktu_mulai_parsed.hour, 'minute': waktu_mulai_parsed.minute},
            'waktu_selesai': {'hour': waktu_selesai_parsed.hour, 'minute': waktu_selesai_parsed.minute},
            'cache_key_combined_logs': cache_key_combined_logs,
            'cache_key_formatted_logs': cache_key_formatted_logs
        }

        session_data = {
            'sessionid': session['sessionid'],
            'csrftoken_cookie': session['csrftoken_cookie'],
            'csrf_token': session['csrf_token']
        }

        # Panggil tugas asinkron untuk update log
        update_log_async.delay(session_data, log_data)

        return redirect(url_for('index'))

    return render_template('edit_log.html', log_id=log_id, log=log_to_edit, formatted_logs=formatted_logs)


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


@app.route('/calendar', methods=['GET'])
def calendar_view():
    if 'sessionid' not in session:
        return redirect(url_for('login_page'))

    session_req = requests.Session()
    session_req.cookies.set('sessionid', session['sessionid'])
    session_req.cookies.set('csrftoken', session['csrftoken_cookie'])

    # Buat cache key berdasarkan session ID
    cache_key_lowongan = f'lowongan_data_{session["sessionid"]}'
    cache_key_latest_period = f'latest_period_{session["sessionid"]}'
    cache_key_combined_logs = f'combined_logs_{session["sessionid"]}'
    cache_key_formatted_logs = f'formatted_logs_{session["sessionid"]}'

    # Cek cache untuk `lowongan_data`
    lowongan_data = cache.get(cache_key_lowongan)
    if lowongan_data is None:
        lowongan_data = get_accepted_lowongan(session_req, session['csrftoken_cookie'], session['sessionid'])
        cache.set(cache_key_lowongan, lowongan_data, timeout=3600 * 12)  # Cache selama 12 jam

    # Cek cache untuk `latest_period`
    latest_period = cache.get(cache_key_latest_period)
    if latest_period is None:
        latest_period = filter_by_latest_period_and_add_create_log(session_req, session['csrftoken_cookie'],
                                                                   session['sessionid'], lowongan_data)
        cache.set(cache_key_latest_period, latest_period, timeout=60)  # Cache selama 1 menit

    # Cek cache untuk `combined_logs`
    combined_logs = cache.get(cache_key_combined_logs)
    if combined_logs is None:
        combined_logs = get_combined_logs_for_latest_period(session_req, session['csrftoken_cookie'],
                                                            session['sessionid'], latest_period)
        cache.set(cache_key_combined_logs, combined_logs, timeout=60)  # Cache selama 1 menit

    # Cek cache untuk `formatted_logs`
    formatted_logs = cache.get(cache_key_formatted_logs)
    if formatted_logs is None:
        formatted_logs = []
        for log in combined_logs:
            if log['Tanggal'] and log['Jam Mulai'] and log['Jam Selesai']:
                day, month, year = log['Tanggal'].split('-')
                formatted_date = f"{year}-{month}-{day}"  # Convert to 'YYYY-MM-DD' format

                formatted_logs.append({
                    'title': log['Kategori'],
                    'start': f"{formatted_date}T{log['Jam Mulai']}",
                    'end': f"{formatted_date}T{log['Jam Selesai']}",
                    'description': log['Deskripsi Tugas'],
                    'duration': str(log['Durasi (Menit)']),
                    'kategori': log['Kategori'],
                    'status': log['Status'],
                    'edit_url': url_for('edit_log_view', log_id=log['LogID']),
                    'course': log.get('Mata Kuliah', 'Unknown Course')
                })
        cache.set(cache_key_formatted_logs, formatted_logs, timeout=60)  # Cache selama 1 menit

    return render_template('calendar.html', formatted_logs=formatted_logs)


@app.route('/update-data', methods=['GET'])
def update_data():
    if 'sessionid' not in session:
        return jsonify({'error': 'User not logged in'}), 401

    session_req = requests.Session()
    session_req.cookies.set('sessionid', session['sessionid'])
    session_req.cookies.set('csrftoken', session['csrftoken_cookie'])

    cache_key_lowongan = f'lowongan_data_{session["sessionid"]}'
    cache_key_latest_period = f'latest_period_{session["sessionid"]}'
    cache_key_combined_logs = f'combined_logs_{session["sessionid"]}'
    cache_key_formatted_logs = f'formatted_logs_{session["sessionid"]}'

    # Fetch and update cache
    lowongan_data = get_accepted_lowongan(session_req, session['csrftoken_cookie'], session['sessionid'])
    cache.set(cache_key_lowongan, lowongan_data, timeout=3600 * 12)  # Cache selama 12 jam

    latest_period = filter_by_latest_period_and_add_create_log(session_req, session['csrftoken_cookie'],
                                                               session['sessionid'], lowongan_data)
    cache.set(cache_key_latest_period, latest_period, timeout=60)  # Cache selama 1 menit

    combined_logs = get_combined_logs_for_latest_period_parallel(session_req, session['csrftoken_cookie'],
                                                                 session['sessionid'], latest_period)
    cache.set(cache_key_combined_logs, combined_logs, timeout=60)  # Cache selama 1 menit

    # Update formatted_logs cache
    formatted_logs = []
    for log in combined_logs:
        if log['Tanggal'] and log['Jam Mulai'] and log['Jam Selesai']:
            day, month, year = log['Tanggal'].split('-')
            formatted_date = f"{year}-{month}-{day}"  # Convert to 'YYYY-MM-DD' format

            formatted_logs.append({
                'title': log['Kategori'],
                'start': f"{formatted_date}T{log['Jam Mulai']}",
                'end': f"{formatted_date}T{log['Jam Selesai']}",
                'description': log['Deskripsi Tugas'],
                'duration': str(log['Durasi (Menit)']),
                'kategori': log['Kategori'],
                'status': log['Status'],
                'edit_url': url_for('edit_log_view', log_id=log['LogID']),
                'course': log.get('Mata Kuliah', 'Unknown Course')
            })
    cache.set(cache_key_formatted_logs, formatted_logs, timeout=60)  # Cache selama 1 menit

    return jsonify({'message': 'Data updated successfully'})


@app.route('/logout', methods=['GET'])
def logout():
    session.clear()  # Menghapus semua data session
    return redirect(url_for('login_page'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
