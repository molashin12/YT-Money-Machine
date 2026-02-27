import requests

url = 'https://api.pullpush.io/reddit/search/submission/?subreddit=AskReddit&sort=desc&size=5'
try:
    resp = requests.get(url, timeout=10)
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json().get('data', [])
        print(f"Posts found: {len(data)}")
        for post in data:
            print(f"- {post.get('title')[:50]}")
    else:
        print(f"Error: {resp.text[:100]}")
except Exception as e:
    print(f"Failed: {e}")
