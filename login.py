import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

COMMON_HEADERS = {
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.100 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Sec-Ch-Ua": "\"Chromium\";v=\"127\", \"Not)A;Brand\";v=\"99\"",
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": "\"Windows\"",
    "Accept-Language": "en-US",
    "Accept-Encoding": "gzip, deflate, br",
    "Priority": "u=0, i"
}


def login(session, username, password):
    """
    Perform login and return the csrf token and session ID.
    """
    login_page_url = "https://siasisten.cs.ui.ac.id/login/"
    login_page_headers = {
        "Cookie": "csrftoken=2QbHxt9YgzqLSNpiWqTeApgwQOTnwxur1kTeOQLPP9qRTHa8YDMDXwB6I3z1F7ol; sc_is_visitor_unique=rx12339556.1724685698.5DDD9564D24F4F8716FA6F31A7C077FC.1.1.1.1.1.1.1.1.1",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        **COMMON_HEADERS
    }

    # Get login page to extract CSRF token
    response = session.get(login_page_url, headers=login_page_headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    csrf_token = soup.find('input', {'name': 'csrfmiddlewaretoken'})['value']
    csrftoken_cookie = response.cookies.get('csrftoken')

    # Perform login
    login_url = "https://siasisten.cs.ui.ac.id/login/"
    post_data = {
        "csrfmiddlewaretoken": csrf_token,
        "username": username,
        "password": password,
        "next": ""
    }
    post_headers = {
        "Cookie": f"csrftoken={csrftoken_cookie}; sc_is_visitor_unique=rx12339556.1724685698.5DDD9564D24F4F8716FA6F31A7C077FC.1.1.1.1.1.1.1.1.1",
        "Cache-Control": "max-age=0",
        "Origin": "https://siasisten.cs.ui.ac.id",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://siasisten.cs.ui.ac.id/login/",
        **COMMON_HEADERS
    }

    login_response = session.post(login_url, headers=post_headers, data=post_data, allow_redirects=False)
    sessionid = login_response.cookies.get('sessionid')
    print(login_response.text)

    return csrf_token, csrftoken_cookie, sessionid


def main():
    # Create a session
    session = requests.Session()

    # Login
    username = os.getenv("USERNAME")
    password = os.getenv("PASSWORD")
    csrf_token, csrftoken_cookie, sessionid = login(session, username, password)
    print(f"CSRF Token: {csrf_token}")
    print(f"CSRF Token Cookie: {csrftoken_cookie}")
    print(f"Session ID: {sessionid}")


if __name__ == "__main__":
    main()
