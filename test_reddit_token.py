import requests
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

url = 'https://www.reddit.com/r/AskReddit/'
try:
    response = requests.get(url, headers=headers)
    print(f"Fetch HTML Status: {response.status_code}")
except Exception as e:
    print(f"Error: {e}")
