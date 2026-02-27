import asyncio
from curl_cffi.requests import AsyncSession

async def test():
    url = 'https://www.reddit.com/r/UnresolvedMysteries/hot.json'
    try:
        async with AsyncSession(impersonate='chrome120') as s:
            resp = await s.get(url, timeout=10)
            print(f'Status: {resp.status_code}, Content-Type: {resp.headers.get("Content-Type")}')
            if resp.status_code == 200:
                print('Success! Snippet:', str(resp.json())[:100])
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test())
