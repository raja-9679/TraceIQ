import asyncio
import httpx
import requests

def test_sync():
    print("\n--- SYNC REQUESTS ---")
    try:
        resp = requests.get("http://127.0.0.1:8000/health", timeout=5)
        print(f"Status: {resp.status_code}")
        print(f"Body: {resp.text}")
    except Exception as e:
        print(f"Error ({type(e).__name__}): {e}")

async def test_async():
    print("\n--- ASYNC HTTPX ---")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://127.0.0.1:8000/health")
            print(f"Status: {resp.status_code}")
            print(f"Body: {resp.text}")
    except Exception as e:
        print(f"Error ({type(e).__name__}): {repr(e)}")

if __name__ == "__main__":
    test_sync()
    asyncio.run(test_async())
