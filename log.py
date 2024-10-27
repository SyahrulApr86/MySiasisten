from http.client import responses

import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
from dotenv import load_dotenv
from login import login
from login import COMMON_HEADERS
from concurrent.futures import ThreadPoolExecutor
import os

# Load environment variables from .env file
load_dotenv()


def get_accepted_lowongan(session, csrftoken_cookie, sessionid):
    """
    Fetch the lowongan data from /log/listLowonganAst and return it as a list of dictionaries.
    """
    lowongan_url = "https://siasisten.cs.ui.ac.id/log/listLowonganAst"
    lowongan_headers = {
        "Host": "siasisten.cs.ui.ac.id",
        "Cookie": f"csrftoken={csrftoken_cookie}; sessionid={sessionid}; sc_is_visitor_unique=rx12339556.1727596004.5DDD9564D24F4F8716FA6F31A7C077FC.2.2.2.2.2.2.2.2.2",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Referer": "https://siasisten.cs.ui.ac.id/keuangan/listPembayaranPerAsisten",
        **COMMON_HEADERS
    }

    # Perform the GET request
    response = session.get(lowongan_url, headers=lowongan_headers)

    # Parse the HTML content
    soup = BeautifulSoup(response.text, 'html.parser')

    # Find the table in the HTML
    table = soup.find('table')
    if not table:
        return []  # Return an empty list if no table is found

    # List to store the rows of data
    data = []

    # Iterate over the rows of the table (skip the header)
    for row in table.find_all('tr')[1:]:
        cols = row.find_all('td')
        if len(cols) == 6:
            no = cols[0].text.strip()
            mata_kuliah = cols[1].text.strip()
            semester = cols[2].text.strip()
            tahun_ajaran = cols[3].text.strip()
            dosen = cols[4].text.strip()
            log_asisten_link = cols[5].find('a')['href'].strip()

            # Append each row as a dictionary
            data.append({
                "No": no,
                "Mata Kuliah": mata_kuliah,
                "Semester": semester,
                "Tahun Ajaran": tahun_ajaran,
                "Dosen": dosen,
                "Log Asisten Link": log_asisten_link,
                "LogID": log_asisten_link.split('/')[-2]
            })

    return data


def fetch_create_log_link(session, csrftoken_cookie, sessionid, log_asisten_link):
    """
    Access the log asisten link and extract the 'Create Log' link from the response.
    """
    log_url = f"https://siasisten.cs.ui.ac.id{log_asisten_link}"
    log_headers = {
        "Host": "siasisten.cs.ui.ac.id",
        "Cookie": f"csrftoken={csrftoken_cookie}; sessionid={sessionid}; sc_is_visitor_unique=rx12339556.1727630974.5DDD9564D24F4F8716FA6F31A7C077FC.3.3.3.3.3.2.2.2.2",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Referer": "https://siasisten.cs.ui.ac.id/log/listLowonganAst",
        **COMMON_HEADERS
    }

    # Perform the GET request to access the log page
    response = session.get(log_url, headers=log_headers)

    # Parse the HTML content
    soup = BeautifulSoup(response.text, 'html.parser')

    # Find the form with the action link to create a new log (Create Log link)
    create_log_form = soup.find('form', {'action': True})

    # If a form exists, return the link; otherwise, return None
    if create_log_form:
        return create_log_form['action']
    return None


def filter_by_latest_period_and_add_create_log(session, csrftoken_cookie, sessionid, lowongan_data):
    """
    Filter lowongan based on the latest period (semester and year of the first entry)
    and add the 'Create Log Link' by accessing each 'Log Asisten Link'.
    """
    if not lowongan_data:
        return []

    # Get the semester and year of the first entry (No 1)
    latest_semester = lowongan_data[0]['Semester']
    latest_tahun_ajaran = lowongan_data[0]['Tahun Ajaran']

    # Filter all entries with the same semester and year
    filtered_data = [
        entry for entry in lowongan_data
        if entry['Semester'] == latest_semester and entry['Tahun Ajaran'] == latest_tahun_ajaran
    ]

    # Fetch 'Create Log Link' for each filtered entry by accessing the 'Log Asisten Link'
    for entry in filtered_data:
        log_asisten_link = entry['Log Asisten Link']
        create_log_link = fetch_create_log_link(session, csrftoken_cookie, sessionid, log_asisten_link)
        entry['Create Log Link'] = create_log_link
        entry['Create Log Link ID'] = create_log_link.split('/')[-2] if create_log_link else None

    return filtered_data


def get_combined_logs_for_latest_period_parallel(session, csrftoken_cookie, sessionid, filtered_lowongan):
    """
    Combine logs for all lowongan in the latest period and return a list of logs with "Mata Kuliah" field added.
    Logs are fetched in parallel and sorted by Tanggal and Jam Mulai in descending order (newest first).
    """
    combined_logs = []

    # Parallel fetching of log data
    with ThreadPoolExecutor() as executor:
        futures = [executor.submit(get_log_per_lowongan, session, csrftoken_cookie, sessionid, lowongan['LogID'])
                   for lowongan in filtered_lowongan]

        for future, lowongan in zip(futures, filtered_lowongan):
            log_mahasiswa_data = future.result()
            mata_kuliah = lowongan['Mata Kuliah']

            # Add "Mata Kuliah" field to each log entry and append to the combined list
            for log_entry in log_mahasiswa_data:
                log_entry['Mata Kuliah'] = mata_kuliah
                combined_logs.append(log_entry)

    # Sort the logs by Tanggal and Jam Mulai (both converted to datetime), newest first
    combined_logs_sorted = sorted(combined_logs, key=lambda log: (
        datetime.strptime(log['Tanggal'], '%d-%m-%Y'),  # Convert 'Tanggal' to datetime
        datetime.strptime(log['Jam Mulai'], '%H:%M')  # Convert 'Jam Mulai' to time
    ), reverse=True)  # Sort in descending order (newest first)

    return combined_logs_sorted


def get_log_per_lowongan(session, csrftoken_cookie, sessionid, log_id):
    """
    Fetch the log data for mahasiswa from /log/listLogMahasiswa/{log_id} and return it as a list of dictionaries.
    Format Tanggal, Jam, and Durasi as per request.
    """
    log_url = f"https://siasisten.cs.ui.ac.id/log/listLogMahasiswa/{log_id}/"
    log_headers = {
        "Host": "siasisten.cs.ui.ac.id",
        "Cookie": f"csrftoken={csrftoken_cookie}; sessionid={sessionid}; sc_is_visitor_unique=rx12339556.1727630974.5DDD9564D24F4F8716FA6F31A7C077FC.3.3.3.3.3.2.2.2.2",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Referer": "https://siasisten.cs.ui.ac.id/log/listLowonganAst",
        **COMMON_HEADERS
    }

    # Perform the GET request
    response = session.get(log_url, headers=log_headers)

    # Parse the HTML content
    soup = BeautifulSoup(response.text, 'html.parser')

    # Find the table in the HTML
    table = soup.find('table')
    if not table:
        return []  # Return an empty list if no table is found

    # List to store the rows of data
    data = []

    # Iterate over the rows of the table (skip the header)
    for row in table.find_all('tr')[1:]:
        cols = row.find_all('td')
        if len(cols) == 8:
            no = cols[0].text.strip()
            tanggal = format_tanggal(cols[1].text.strip())
            jam_mulai, jam_selesai, durasi = parse_jam_durasi(cols[2].text.strip())
            kategori = cols[3].text.strip()
            deskripsi_tugas = cols[4].text.strip()
            status = cols[5].text.strip()
            operation = cols[6].text.strip()
            pesan_link = cols[7].find('a')['href'].strip()

            # Append each row as a dictionary
            data.append({
                "No": no,
                "Tanggal": tanggal,
                "Jam Mulai": jam_mulai,
                "Jam Selesai": jam_selesai,
                "Durasi (Menit)": durasi,
                "Kategori": kategori,
                "Deskripsi Tugas": deskripsi_tugas,
                "Status": status,
                "Operation": operation,
                "Pesan Link": pesan_link,
                "LogID": pesan_link.split('/')[-2]
            })

    return data


def format_tanggal(tanggal_str):
    """
    Convert date string from '29 Sep 2024' to '29-09-2024'.
    """
    date_obj = datetime.strptime(tanggal_str, "%d %b %Y")
    return date_obj.strftime("%d-%m-%Y")


def parse_jam_durasi(jam_str):
    """
    Parse the jam string to extract start time, end time, and duration.
    Example input: '19:00 - 21:30 \n 2 jam 30 menit' or '19:00 - 21:30  2 jam 30 menit'
    Returns: (jam_mulai, jam_selesai, durasi_in_minutes)
    """
    # Cari rentang jam menggunakan karakter '-'
    if '-' in jam_str:
        jam_range = jam_str.split('-')
        jam_mulai = jam_range[0].strip()

        if len(jam_range) > 1:
            jam_selesai = jam_range[1].split()[0].strip()  # Ambil bagian setelah "-" hingga spasi pertama
        else:
            jam_selesai = None  # Jika tidak ada waktu selesai, buat default None

        # Cari durasi setelah rentang waktu (misal: '2 jam 30 menit')
        durasi_str = jam_str.split(jam_selesai)[-1].strip() if jam_selesai else ''
        durasi = parse_durasi(durasi_str)
    else:
        # Jika format tidak sesuai, default semua nilai
        jam_mulai = ''
        jam_selesai = ''
        durasi = 0

    return jam_mulai, jam_selesai, durasi


def parse_durasi(durasi_str):
    """
    Convert duration string like '2 jam 30 menit' to total minutes.
    """
    if not durasi_str:  # In case no duration is found
        return 0

    durasi_parts = durasi_str.split()
    total_minutes = 0

    if 'jam' in durasi_parts:
        total_minutes += int(durasi_parts[durasi_parts.index('jam') - 1]) * 60
    if 'menit' in durasi_parts:
        total_minutes += int(durasi_parts[durasi_parts.index('menit') - 1])

    return total_minutes


from datetime import datetime


def get_combined_logs_for_latest_period(session, csrftoken_cookie, sessionid, filtered_lowongan):
    """
    Combine logs for all lowongan in the latest period and return a list of logs with "Mata Kuliah" field added.
    Logs are sorted by Tanggal and Jam Mulai in descending order (newest first).
    """
    combined_logs = []

    # Iterate over each lowongan in the filtered list
    for lowongan in filtered_lowongan:
        log_id = lowongan['LogID']
        mata_kuliah = lowongan['Mata Kuliah']

        # Fetch the logs for the current lowongan
        log_mahasiswa_data = get_log_per_lowongan(session, csrftoken_cookie, sessionid, log_id)

        # Add "Mata Kuliah" field to each log entry and append to the combined list
        for log_entry in log_mahasiswa_data:
            log_entry['Mata Kuliah'] = mata_kuliah
            combined_logs.append(log_entry)

    # Sort the logs by Tanggal and Jam Mulai (both converted to datetime), newest first
    combined_logs_sorted = sorted(combined_logs, key=lambda log: (
        datetime.strptime(log['Tanggal'], '%d-%m-%Y'),  # Convert 'Tanggal' to datetime
        datetime.strptime(log['Jam Mulai'], '%H:%M')  # Convert 'Jam Mulai' to time
    ), reverse=True)  # Sort in descending order (newest first)

    return combined_logs_sorted


def is_overlap(logs):
    """
    Check if any log entries have overlapping time intervals.
    Returns a boolean indicating overlap and a list of overlapping logs (empty if no overlap).
    """
    overlap_logs = []

    # Convert "Tanggal", "Jam Mulai", and "Jam Selesai" to datetime objects for comparison
    for i in range(len(logs)):
        log1 = logs[i]
        try:
            # Handle logs with the format '%d-%m-%Y %H:%M'
            log1_start = datetime.strptime(log1['Tanggal'] + ' ' + log1['Jam Mulai'], '%d-%m-%Y %H:%M')
            log1_end = datetime.strptime(log1['Tanggal'] + ' ' + log1['Jam Selesai'], '%d-%m-%Y %H:%M')
        except ValueError:
            # If the format doesn't match, try the alternate format '%Y-%m-%d %H:%M'
            log1_start = datetime.strptime(log1['Tanggal'] + ' ' + log1['Jam Mulai'], '%Y-%m-%d %H:%M')
            log1_end = datetime.strptime(log1['Tanggal'] + ' ' + log1['Jam Selesai'], '%Y-%m-%d %H:%M')

        for j in range(i + 1, len(logs)):
            log2 = logs[j]
            try:
                # Handle logs with the format '%d-%m-%Y %H:%M'
                log2_start = datetime.strptime(log2['Tanggal'] + ' ' + log2['Jam Mulai'], '%d-%m-%Y %H:%M')
                log2_end = datetime.strptime(log2['Tanggal'] + ' ' + log2['Jam Selesai'], '%d-%m-%Y %H:%M')
            except ValueError:
                # If the format doesn't match, try the alternate format '%Y-%m-%d %H:%M'
                log2_start = datetime.strptime(log2['Tanggal'] + ' ' + log2['Jam Mulai'], '%Y-%m-%d %H:%M')
                log2_end = datetime.strptime(log2['Tanggal'] + ' ' + log2['Jam Selesai'], '%Y-%m-%d %H:%M')

            # Check for overlap between the two logs
            if (log1_start < log2_end) and (log1_end > log2_start):
                overlap_logs.append((log1, log2))

    # If we found overlapping logs, return True and the list of overlaps
    if overlap_logs:
        return True, overlap_logs

    # No overlaps found, return False and an empty list
    return False, []


def create_log(session, csrftoken_cookie, sessionid, csrfmiddlewaretoken, create_log_id, kategori_log, deskripsi,
               tanggal, waktu_mulai, waktu_selesai):
    """
    Simulate a POST request to create a log entry for a specific log ID.

    Parameters:
    - session: The requests session object
    - csrftoken_cookie: The csrf token from the cookie
    - sessionid: The session ID
    - csrfmiddlewaretoken: The csrf token from the form
    - log_id: The log ID from the create log link (e.g., 8879)
    - kategori_log: The log category ID (1-15 based on the options provided)
    - deskripsi: Description of the log entry
    - tanggal: A dictionary containing 'day', 'month', 'year' for the log date
    - waktu_mulai: A dictionary containing 'hour', 'minute' for the start time
    - waktu_selesai: A dictionary containing 'hour', 'minute' for the end time

    LOG_CATEGORIES = {
    "1": "Asistensi/Tutorial",
    "2": "Mengoreksi",
    "3": "Mengawas",
    "5": "Persiapan Asistensi",
    "6": "Membuat soal/tugas",
    "7": "Rapat",
    "8": "Sit in Kelas",
    "9": "Pengembangan Materi",
    "10": "Pengembangan apps",
    "11": "Persiapan infra",
    "12": "Dokumentasi",
    "13": "Persiapan kuliah",
    "14": "Penunjang",
    "15": "Input Data"
}
    """
    create_log_url = f"https://siasisten.cs.ui.ac.id/log/create/{create_log_id}/"
    create_log_headers = {
        "Host": "siasisten.cs.ui.ac.id",
        "Cookie": f"csrftoken={csrftoken_cookie}; sessionid={sessionid}; sc_is_visitor_unique=rx12339556.1727636078.5DDD9564D24F4F8716FA6F31A7C077FC.5.3.3.3.3.2.2.2.2",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": "\"Chromium\";v=\"127\", \"Not)A;Brand\";v=\"99\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Accept-Language": "en-US",
        "Upgrade-Insecure-Requests": "1",
        "Origin": f"https://siasisten.cs.ui.ac.id/log/create/{create_log_id}/",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.100 Safari/537.36",
        "Referer": f"https://siasisten.cs.ui.ac.id/log/create/{create_log_id}/",
        **COMMON_HEADERS
    }

    # Data to be sent in the POST request
    create_log_data = {
        "csrfmiddlewaretoken": csrfmiddlewaretoken,
        "kategori_log": kategori_log,
        "deskripsi": deskripsi,
        "tanggal_day": tanggal['day'],
        "tanggal_month": tanggal['month'],
        "tanggal_year": tanggal['year'],
        "waktu_mulai_hour": waktu_mulai['hour'],
        "waktu_mulai_minute": waktu_mulai['minute'],
        "waktu_selesai_hour": waktu_selesai['hour'],
        "waktu_selesai_minute": waktu_selesai['minute']
    }

    # Perform the POST request to create the log
    response = session.post(create_log_url, headers=create_log_headers, data=create_log_data)

    # Check if the request was successful
    if response.status_code == 200:
        print(f"Log created successfully for Log ID {create_log_id}")
    else:
        print(f"Failed to create log for Log ID {create_log_id}: {response.status_code}")
    return response


def update_log(session, csrftoken_cookie, sessionid, csrfmiddlewaretoken, log_id, kategori_log, deskripsi, tanggal,
               waktu_mulai, waktu_selesai):
    """
    Simulate a POST request to update a log entry for a specific log ID.

    Parameters:
    - session: The requests session object
    - csrftoken_cookie: The csrf token from the cookie
    - sessionid: The session ID
    - csrfmiddlewaretoken: The csrf token from the form
    - log_id: The log ID from the update log link (e.g., 261366)
    - kategori_log: The log category ID (1-15 based on the options provided)
    - deskripsi: Description of the log entry
    - tanggal: A dictionary containing 'day', 'month', 'year' for the log date
    - waktu_mulai: A dictionary containing 'hour', 'minute' for the start time
    - waktu_selesai: A dictionary containing 'hour', 'minute' for the end time
    """
    update_log_url = f"https://siasisten.cs.ui.ac.id/log/update/{log_id}/"
    update_log_headers = {
        "Host": "siasisten.cs.ui.ac.id",
        "Cookie": f"csrftoken={csrftoken_cookie}; sessionid={sessionid}; sc_is_visitor_unique=rx12339556.1727668608.5DDD9564D24F4F8716FA6F31A7C077FC.6.4.4.4.4.2.2.2.2",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": "\"Chromium\";v=\"127\", \"Not)A;Brand\";v=\"99\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Accept-Language": "en-US",
        "Upgrade-Insecure-Requests": "1",
        "Origin": f"https://siasisten.cs.ui.ac.id/log/update/{log_id}/",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.100 Safari/537.36",
        "Referer": f"https://siasisten.cs.ui.ac.id/log/update/{log_id}/",
        **COMMON_HEADERS
    }

    # Data to be sent in the POST request
    update_log_data = {
        "csrfmiddlewaretoken": csrfmiddlewaretoken,
        "kategori_log": kategori_log,
        "deskripsi": deskripsi,
        "tanggal_day": tanggal['day'],
        "tanggal_month": tanggal['month'],
        "tanggal_year": tanggal['year'],
        "waktu_mulai_hour": waktu_mulai['hour'],
        "waktu_mulai_minute": waktu_mulai['minute'],
        "waktu_selesai_hour": waktu_selesai['hour'],
        "waktu_selesai_minute": waktu_selesai['minute']
    }

    # Perform the POST request to update the log
    response = session.post(update_log_url, headers=update_log_headers, data=update_log_data)

    # Check if the request was successful
    if response.status_code == 200:
        print(f"Log updated successfully for Log ID {log_id}")
    else:
        print(f"Failed to update log for Log ID {log_id}: {response.status_code}")
    return response

def delete_log(session, csrftoken_cookie, sessionid, csrfmiddlewaretoken, log_id):
    """
    Simulate a POST request to delete a log entry for a specific log ID.

    Parameters:
    - session: The requests session object
    - csrftoken_cookie: The csrf token from the cookie
    - sessionid: The session ID
    - csrfmiddlewaretoken: The csrf token from the form
    - log_id: The log ID from the delete log link (e.g., 261366)
    """
    delete_log_url = f"https://siasisten.cs.ui.ac.id/log/delete/{log_id}/"
    delete_log_headers = {
        "Host": "siasisten.cs.ui.ac.id",
        "Cookie": f"csrftoken={csrftoken_cookie}; sessionid={sessionid}; sc_is_visitor_unique=rx12339556.1727668826.5DDD9564D24F4F8716FA6F31A7C077FC.6.4.4.4.4.2.2.2.2",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": "\"Chromium\";v=\"127\", \"Not)A;Brand\";v=\"99\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Accept-Language": "en-US",
        "Upgrade-Insecure-Requests": "1",
        "Origin": f"https://siasisten.cs.ui.ac.id/log/delete/{log_id}/",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.100 Safari/537.36",
        "Referer": f"https://siasisten.cs.ui.ac.id/log/delete/{log_id}/",
        **COMMON_HEADERS
    }

    # Data to be sent in the POST request
    delete_log_data = {
        "csrfmiddlewaretoken": csrfmiddlewaretoken
    }

    # Perform the POST request to delete the log
    response = session.post(delete_log_url, headers=delete_log_headers, data=delete_log_data)

    # Check if the request was successful
    if response.status_code == 200:
        print(f"Log deleted successfully for Log ID {log_id}")
    else:
        print(f"Failed to delete log for Log ID {log_id}: {response.status_code}")
    return response


def main():
    # Create a session
    session = requests.Session()

    # Login
    username = os.getenv("USER")
    password = os.getenv("PASSWORD")
    csrf_token, csrftoken_cookie, sessionid = login(session, username, password)

    # Get the lowongan data
    lowongan_data = get_accepted_lowongan(session, csrftoken_cookie, sessionid)

    # Print the lowongan data
    print("All lowongan data:")
    print(json.dumps(lowongan_data, indent=4))

    # Filter data based on the latest period (semester and year from the first entry)
    filtered_lowongan = filter_by_latest_period_and_add_create_log(session, csrftoken_cookie, sessionid, lowongan_data)

    # Print the filtered lowongan data
    print("Filtered lowongan data (same period as latest):")
    print(json.dumps(filtered_lowongan, indent=4))

    # Combine logs for all lowongan in the latest period
    combined_logs = get_combined_logs_for_latest_period(session, csrftoken_cookie, sessionid, filtered_lowongan)

    # Print the combined log data
    print("Combined logs with 'Mata Kuliah' field:")
    print(json.dumps(combined_logs, indent=4))

    # Check for overlapping logs
    overlap, overlap_logs = is_overlap(combined_logs)

    if overlap:
        print("There are overlapping logs:")
        for log1, log2 in overlap_logs:
            print(f"Log 1: {log1}")
            print(f"Log 2: {log2}")
    else:
        print("No overlapping logs found.")

    log_id = "261367"  # Example log ID
    kategori_log = "1"  # 'Rapat' category
    deskripsi = "Mengadakan rapat untuk membahas perkembangan project tes"
    tanggal = {"day": "30", "month": "9", "year": "2024"}
    waktu_mulai = {"hour": "11", "minute": "00"}
    waktu_selesai = {"hour": "11", "minute": "30"}

    # Call the function to create the log
    # response = create_log(session, csrftoken_cookie, sessionid, csrf_token, log_id, kategori_log, deskripsi, tanggal,
    #                       waktu_mulai, waktu_selesai)
    # #
    # # Print the response (optional)
    # print(response.text)

    # response = update_log(session, csrftoken_cookie, sessionid, csrf_token, log_id, kategori_log, deskripsi, tanggal,
    #                       waktu_mulai, waktu_selesai)

    # response = delete_log(session, csrftoken_cookie, sessionid, csrf_token, log_id)


if __name__ == "__main__":
    main()
