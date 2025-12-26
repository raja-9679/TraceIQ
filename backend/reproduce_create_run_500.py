import asyncio
import httpx
import os

# The user provided URL: http://localhost:8000/api/runs?suite_id=15&browser=chromium&case_id=6&device=Desktop
# This is a POST request.

async def reproduce_create_run_500():
    url = "http://localhost:8000/api/runs"
    params = {
        "suite_id": 15,
        "browser": "chromium",
        "case_id": 6,
        "device": "Desktop"
    }
    
    print(f"Sending POST request to {url} with params {params}")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, params=params)
            print(f"Status Code: {response.status_code}")
            print(f"Response Text: {response.text}")
        except Exception as e:
            print(f"Request failed: {e}")

if __name__ == "__main__":
    asyncio.run(reproduce_create_run_500())
