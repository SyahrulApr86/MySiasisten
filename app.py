from dateutil.relativedelta import relativedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash
import pytz
from datetime import time
import time as t
from flask import jsonify
from flask_caching import Cache
from celery import Celery
from log import *
from keuangan import get_keuangan_data, calculate_total_pembayaran, clean_currency
from models import KeuanganData, db
import os

# Get the absolute path of the current directory
basedir = os.path.abspath(os.path.dirname(__file__))

# Create instance directory if it doesn't exist
instance_path = os.path.join(basedir, 'instance')
if not os.path.exists(instance_path):
    os.makedirs(instance_path)


def make_celery(app):
    celery = Celery(app.import_name, backend=app.config['CELERY_RESULT_BACKEND'],
                    broker=app.config['CELERY_BROKER_URL'])
    celery.conf.update(app.config)
    return celery


# Initialize Flask app
app = Flask(__name__, instance_path=instance_path)
app.secret_key = os.urandom(24)

# Redis Configuration
app.config['CACHE_TYPE'] = 'RedisCache'
app.config['CACHE_REDIS_HOST'] = 'redis'
app.config['CACHE_REDIS_PORT'] = 6379
app.config['CACHE_REDIS_DB'] = 0
app.config['CACHE_REDIS_URL'] = 'redis://redis:6379/0'
app.config['CACHE_DEFAULT_TIMEOUT'] = 300

# Database Configuration
db_path = os.path.join(instance_path, 'keuangan.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Celery Configuration
app.config.update(
    CELERY_BROKER_URL='redis://redis:6379/0',
    CELERY_RESULT_BACKEND='redis://redis:6379/0'
)

# Initialize extensions
cache = Cache(app)
celery = make_celery(app)
db.init_app(app)

# Create database tables
with app.app_context():
    try:
        db.create_all()
        print(f"Database created successfully at {db_path}")
    except Exception as e:
        print(f"Error creating database: {str(e)}")
        # Check if directory is writable
        if not os.access(instance_path, os.W_OK):
            print(f"Warning: No write permission in {instance_path}")
        # Check directory permissions
        print(f"Directory permissions: {oct(os.stat(instance_path).st_mode)[-3:]}")


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
        # overlap, overlap_logs = is_overlap(combined_logs)
        # if overlap:
        #     overlap_message = "Log berikut menyebabkan overlap:<br>"
        #     for log1, log2 in overlap_logs:
        #         log1_course = log1.get('Mata Kuliah', 'Unknown Course')
        #         overlap_message += f"<strong>{log1_course}:</strong> {log1['Tanggal']} {log1['Jam Mulai']} - {log1['Jam Selesai']}<br><br>"
        #     return render_template('create_log.html',
        #                            create_log_id=create_log_id,
        #                            error_message=overlap_message,
        #                            formatted_logs=formatted_logs)

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

    if log_to_edit and log_to_edit['Status'] in ['disetujui dosen/TA', 'diproses']:
        return redirect(url_for('index'))

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
        # overlap, overlap_logs = is_overlap(combined_logs)
        # if overlap:
        #     overlap_message = "Log berikut menyebabkan overlap:<br>"
        #     for log1, log2 in overlap_logs:
        #         log1_course = log1.get('Mata Kuliah', 'Unknown Course')
        #         overlap_message += f"<strong>{log1_course}:</strong> {log1['Tanggal']} {log1['Jam Mulai']} - {log1['Jam Selesai']}<br><br>"
        #     return render_template('edit_log.html',
        #                            log_id=log_id,
        #                            error_message=overlap_message,
        #                            log=log_to_edit,
        #                            formatted_logs=formatted_logs)

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
    error_message = None

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        session_req = requests.Session()
        csrf_token, csrftoken_cookie, sessionid = login(session_req, username, password)

        if sessionid:
            session['username'] = username
            session['csrf_token'] = csrf_token
            session['sessionid'] = sessionid
            session['csrftoken_cookie'] = csrftoken_cookie

            # Start historical data fetch task
            session_data = {
                'csrf_token': csrf_token,
                'csrftoken_cookie': csrftoken_cookie,
                'sessionid': sessionid
            }

            fetch_historical_keuangan.delay(username, session_data)

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

    # Cache keys
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

    # Update keuangan data cache for the current and previous month
    current_date = datetime.now()
    current_year = current_date.year
    current_month = current_date.month

    # Cache current month keuangan data
    cache_key_current = f'keuangan_data_{session["sessionid"]}_{current_year}_{current_month}'
    keuangan_data_current = get_keuangan_data(
        username=session['username'],
        session=session_req,
        csrf_token=session['csrf_token'],
        csrftoken_cookie=session['csrftoken_cookie'],
        sessionid=session['sessionid'],
        year=current_year,
        month=current_month
    )
    if keuangan_data_current:
        cache.set(cache_key_current, keuangan_data_current, timeout=60)

    # Handle previous month with correct year
    if current_month == 1:
        previous_month = 12
        previous_year = current_year - 1
    else:
        previous_month = current_month - 1
        previous_year = current_year

    # Cache previous month keuangan data
    cache_key_previous = f'keuangan_data_{session["sessionid"]}_{previous_year}_{previous_month}'
    keuangan_data_previous = get_keuangan_data(
        username=session['username'],
        session=session_req,
        csrf_token=session['csrf_token'],
        csrftoken_cookie=session['csrftoken_cookie'],
        sessionid=session['sessionid'],
        year=previous_year,
        month=previous_month
    )
    if keuangan_data_previous:
        cache.set(cache_key_previous, keuangan_data_previous, timeout=60)

    return jsonify({'message': 'Data updated successfully'})


@celery.task(name="app.fetch_historical_keuangan")
def fetch_historical_keuangan(username, session_data):
    with app.app_context():
        app.logger.info(f"Fetching historical data for {username}")

        session_req = requests.Session()
        session_req.cookies.set('sessionid', session_data['sessionid'])
        session_req.cookies.set('csrftoken', session_data['csrftoken_cookie'])

        current_date = datetime.now()
        start_year = 2021
        end_year = current_date.year

        try:
            # Fetch dan simpan data historis
            for year in range(start_year, end_year + 1):
                for month in range(1, 13):
                    app.logger.info(f"Fetching data for {year}-{month}")

                    # Skip future months
                    if year == current_date.year and month > current_date.month:
                        continue

                    # Cek apakah data sudah ada di DB
                    existing_data = KeuanganData.query.filter_by(
                        username=username,
                        tahun=year,
                        bulan=month
                    ).first()

                    if not existing_data:
                        data = get_keuangan_data(
                            username=username,
                            session=session_req,
                            csrf_token=session_data['csrf_token'],
                            csrftoken_cookie=session_data['csrftoken_cookie'],
                            sessionid=session_data['sessionid'],
                            year=year,
                            month=month
                        )

                        if data:
                            for entry in data:
                                keuangan = KeuanganData(
                                    username=username,
                                    npm=entry['NPM'],
                                    asisten=entry['Asisten'],
                                    tahun=year,
                                    bulan=month,
                                    mata_kuliah=entry['Mata_Kuliah'],
                                    jumlah_jam=float(entry['Jumlah_Jam'].replace(' Jam', '')),
                                    honor_per_jam=clean_currency(entry['Honor_Per_Jam']),
                                    jumlah_pembayaran=clean_currency(entry['Jumlah_Pembayaran']),
                                    status=entry['Status']
                                )
                                db.session.add(keuangan)

                            db.session.commit()

                    # Delay untuk menghindari rate limiting
                    t.sleep(1)

        except Exception as e:
            app.logger.error(f"Error fetching historical data: {str(e)}")
            raise


@app.route('/keuangan', methods=['GET', 'POST'])
def keuangan_view():
    if 'sessionid' not in session:
        return redirect(url_for('login_page'))

    # Buat session request
    session_req = requests.Session()
    session_req.cookies.set('sessionid', session['sessionid'])
    session_req.cookies.set('csrftoken', session['csrftoken_cookie'])

    # Setup date variables
    current_date = datetime.now()
    current_year = current_date.year

    # Data untuk dropdown
    years = range(current_year - 5, current_year + 2)
    months = [
        (1, 'Januari'), (2, 'Februari'), (3, 'Maret'),
        (4, 'April'), (5, 'Mei'), (6, 'Juni'),
        (7, 'Juli'), (8, 'Agustus'), (9, 'September'),
        (10, 'Oktober'), (11, 'November'), (12, 'Desember')
    ]

    # Inisialisasi variabel
    keuangan_data = None
    total_pembayaran = 0
    status_totals = {}
    selected_year = current_year
    selected_month = current_date.month

    if request.method == 'POST':
        try:
            selected_year = int(request.form.get('year'))
            selected_month = int(request.form.get('month'))

            # Selalu fetch dari siasisten untuk data detail pembayaran
            cache_key = f'keuangan_data_{session["sessionid"]}_{selected_year}_{selected_month}'
            keuangan_data = cache.get(cache_key)

            if keuangan_data is None:
                keuangan_data = get_keuangan_data(
                    username=session['username'],
                    session=session_req,
                    csrf_token=session['csrf_token'],
                    csrftoken_cookie=session['csrftoken_cookie'],
                    sessionid=session['sessionid'],
                    year=selected_year,
                    month=selected_month
                )
                if keuangan_data:
                    cache.set(cache_key, keuangan_data, timeout=60)

            if keuangan_data:
                total_pembayaran, status_totals = calculate_total_pembayaran(keuangan_data)
            else:
                flash('Tidak ada data keuangan untuk periode yang dipilih.', 'info')

        except Exception as e:
            flash(f"Error mengambil data: {str(e)}", 'error')
            app.logger.error(f"Error in keuangan_view: {str(e)}")

    # Ambil data statistik untuk chart dari database
    try:
        stats = db.session.query(
            KeuanganData.tahun,
            KeuanganData.bulan,
            db.func.sum(KeuanganData.jumlah_pembayaran).label('total')
        ).filter_by(
            username=session['username']
        ).group_by(
            KeuanganData.tahun,
            KeuanganData.bulan
        ).order_by(
            KeuanganData.tahun,
            KeuanganData.bulan
        ).all()

        # Format data untuk chart
        chart_data = {
            'labels': [f"{row.tahun}-{row.bulan:02d}" for row in stats],
            'data': [float(row.total) for row in stats]
        }

        # Hitung statistik tambahan
        if stats:
            total_all_time = sum(float(row.total) for row in stats)
            avg_monthly = total_all_time / len(stats)
            max_monthly = max(float(row.total) for row in stats)
            min_monthly = min(float(row.total) for row in stats)

            summary_stats = {
                'total_all_time': total_all_time,
                'avg_monthly': avg_monthly,
                'max_monthly': max_monthly,
                'min_monthly': min_monthly,
                'total_months': len(stats)
            }
        else:
            summary_stats = None
            chart_data = {'labels': [], 'data': []}

    except Exception as e:
        app.logger.error(f"Error fetching statistics: {str(e)}")
        summary_stats = None
        chart_data = {'labels': [], 'data': []}

    # Get status untuk progress fetch historis
    fetch_status = cache.get(f'fetch_status_{session["username"]}')

    return render_template(
        'keuangan.html',
        keuangan_data=keuangan_data,
        total_pembayaran=total_pembayaran,
        status_totals=status_totals,
        years=years,
        months=months,
        selected_year=selected_year,
        selected_month=selected_month,
        current_year=current_year,
        current_month=current_date.month,
        chart_data=chart_data,
        summary_stats=summary_stats,
        fetch_status=fetch_status
    )


@app.route('/logout', methods=['GET'])
def logout():
    session.clear()  # Menghapus semua data session
    return redirect(url_for('login_page'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
