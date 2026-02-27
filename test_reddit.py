import urllib.request
import json
import ssl

ssl._create_default_https_context = ssl._create_unverified_context

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

urls = [
    'https://www.reddit.com/r/UnresolvedMysteries/hot.json',
    'https://old.reddit.com/r/UnresolvedMysteries/hot.json',
    'https://api.reddit.com/r/UnresolvedMysteries/hot.json',
    'https://www.reddit.com/r/UnresolvedMysteries.rss'
]

for url in urls:
    print(f"Testing {url}...")
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            print(f"Success! Status: {response.status}, Content-Type: {response.getheader('Content-Type')}")
            data = response.read()
            print(f"Preview: {data[:100]}")
    except Exception as e:
        print(f"Failed: {e}")
    print("-" * 40)
