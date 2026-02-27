import requests

instances = [
    'https://l.opnxng.com/r/AskReddit.json',
    'https://redlib.ducks.party/r/AskReddit.json',
    'https://libreddit.freedit.eu/r/AskReddit.json',
    'https://libreddit.pussthecat.org/r/AskReddit.json',
    'https://reddit.rtrace.io/r/AskReddit.json'
]

headers = {'User-Agent': 'Mozilla/5.0'}

for url in instances:
    print(f"Testing {url}...")
    try:
        r = requests.get(url, headers=headers, timeout=5)
        print(f"Status: {r.status_code}")
        if r.status_code == 200:
            print("Success! JSON length:", len(r.json()))
            break
    except Exception as e:
        print(f"Failed: {e}")
    print("-" * 20)
