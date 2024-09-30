import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
from dotenv import load_dotenv
from login import login
from login import COMMON_HEADERS
import os

load_dotenv()

def get_keuangan_data(username, session, csrf_token, csrftoken_cookie, sessionid, year, month):
    """
    Fetch the financial data for a specific year and month.
    """
    keuangan_url = "https://siasisten.cs.ui.ac.id/keuangan/listPembayaranPerAsisten"
    keuangan_headers = {
        "Host": "siasisten.cs.ui.ac.id",
        "Cookie": f"csrftoken={csrftoken_cookie}; sessionid={sessionid}; sc_is_visitor_unique=rx12339556.1727594531.5DDD9564D24F4F8716FA6F31A7C077FC.2.2.2.2.2.2.2.2.2",
        "Content-Length": "143",
        "Cache-Control": "max-age=0",
        "Origin": "https://siasisten.cs.ui.ac.id",
        "Content-Type": "application/x-www-form-urlencoded",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Referer": "https://siasisten.cs.ui.ac.id/keuangan/listPembayaranPerAsisten",
        **COMMON_HEADERS
    }
    keuangan_data = {
        "csrfmiddlewaretoken": csrf_token,
        "tahun": str(year),
        "bulan": str(month),
        "username": username,
        "statusid": "-1"
    }

    response = session.post(keuangan_url, headers=keuangan_headers, data=keuangan_data)
    soup = BeautifulSoup(response.text, 'html.parser')
    tables = soup.find_all('table')

    data = []
    for table in tables:
        headers = [th.text.strip() for th in table.find_all('th')]
        if 'NPM' in headers and 'Asisten' in headers:
            for row in table.find_all('tr')[1:]:
                cols = row.find_all('td')
                if len(cols) == 8:
                    npm = cols[0].text.strip()
                    asisten = cols[1].text.strip()
                    bulan_text = cols[2].text.strip()
                    mata_kuliah = cols[3].text.strip()
                    jumlah_jam = cols[4].text.strip()
                    honor_per_jam = cols[5].text.strip()
                    jumlah_pembayaran = cols[6].text.strip()
                    status = cols[7].text.strip()

                    data.append({
                        "NPM": npm,
                        "Asisten": asisten,
                        "Bulan": bulan_text,
                        "Mata Kuliah": mata_kuliah,
                        "Jumlah Jam": jumlah_jam,
                        "Honor Per Jam": honor_per_jam,
                        "Jumlah Pembayaran": jumlah_pembayaran,
                        "Status": status
                    })
    return data


def clean_currency(value):
    """
    Clean currency string and convert to float.
    """
    return float(value.replace("Rp", "").replace(".", "").replace(",", ".").strip())


def calculate_total_pembayaran(data, status='diproses'):
    """
    Calculate total payment for a specific status.
    """
    total = 0
    for entry in data:
        if entry['Status'].lower() == status:
            try:
                total += clean_currency(entry['Jumlah Pembayaran'])
            except ValueError:
                print(f"Invalid number format for {entry['Jumlah Pembayaran']}")
    return total


def save_to_json(data, filename):
    """
    Save data to a JSON file.
    """
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)
    print(f"Data saved to {filename}")


def get_all_keuangan_data(session, username, password):
    """
    Fetch all financial data from June 2021 to the current month.
    :return: List of dictionaries containing the financial data.
    """
    csrf_token, csrftoken_cookie, sessionid = login(session, username, password)

    all_data = []
    now = datetime.now()
    month_to_pembayaran = dict()

    # Fetch data from June 2021 to the current month
    for year in range(2021, now.year + 1):
        for month in range(6 if year == 2021 else 1, now.month + 1 if year == now.year else 13):
            data = get_keuangan_data(username, session, csrf_token, csrftoken_cookie, sessionid, year, month)
            if data:

                all_data.extend(data)
                total_bulan_diproses = calculate_total_pembayaran(data)
                month_to_pembayaran[(year, month)] = total_bulan_diproses

    # Save all data to JSON
    save_to_json(all_data, 'keuangan_history.json')

    # Calculate overall total
    total_pembayaran_diproses = calculate_total_pembayaran(all_data)
    return all_data, total_pembayaran_diproses, month_to_pembayaran

def main():
    # Initialize session and login
    session = requests.Session()
    username = os.getenv("USER")
    password = os.getenv("PASSWORD")

    all_data, total_pembayaran_diproses, month_to_pembayaran = get_all_keuangan_data(session, username, password)
    print(f"Total Pembayaran (Diproses): Rp{total_pembayaran_diproses:,.2f}")
    print("Monthly Payment:")
    for key, value in month_to_pembayaran.items():
        print(f"{key[0]}-{key[1]:02d}: Rp{value:,.2f}")

    print("All data saved to keuangan_history.json")
    for entry in all_data:
        print(entry)

if __name__ == "__main__":
    main()
