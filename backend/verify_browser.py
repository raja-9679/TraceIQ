import requests
import sys
import time
import json

BASE_URL = "http://localhost:8000"

def main():
    # Login
    print("Logging in...")
    resp = requests.post(f"{BASE_URL}/api/auth/login", data={"username": "debug_v1@example.com", "password": "password"})
    if resp.status_code != 200:
        print("Login failed:", resp.text)
        sys.exit(1)
    
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("Login successful.")
    
    # Create Suite with Firefox
    print("Creating Firefox Suite...")
    suite_data = {
        "name": "Firefox Suite",
        "description": "Verification for Firefox",
        "browser": "firefox", # Key field
        "execution_mode": "continuous"
    }
    resp = requests.post(f"{BASE_URL}/api/suites", json=suite_data, headers=headers)
    if resp.status_code != 200:
        print("Create Suite failed:", resp.text)
        sys.exit(1)
    suite = resp.json()
    suite_id = suite["id"]
    print(f"Suite created: {suite_id}")

    # Create Test Case
    print("Creating Test Case...")
    case_data = {
        "name": "Google Visit",
        "test_suite_id": suite_id,
        "steps": [
            {"id": "s1", "type": "goto", "value": "https://www.google.com"},
            {"id": "s2", "type": "wait-timeout", "value": "1000"}
        ]
    }
    resp = requests.post(f"{BASE_URL}/api/suites/{suite_id}/cases", json=case_data, headers=headers)
    if resp.status_code != 200:
        print("Create Case failed:", resp.text)
        sys.exit(1)
    case_id = resp.json()["id"]
    print(f"Case created: {case_id}")

    # Create Run
    print("Starting Run...")
    resp = requests.post(f"{BASE_URL}/api/runs?suite_id={suite_id}", headers=headers)
    if resp.status_code != 200:
        print("Start Run failed:", resp.text)
        sys.exit(1)
    run = resp.json()
    run_id = run["id"]
    print(f"Run started: {run_id}")

    # Poll for completion
    print("Polling for completion...")
    for _ in range(30):
        resp = requests.get(f"{BASE_URL}/api/runs/{run_id}", headers=headers)
        status = resp.json()["status"]
        print(f"Status: {status}")
        if status in ["passed", "failed", "error"]:
            break
        time.sleep(1)
    
    if status == "passed":
        print("Test PASSED!")
    else:
        print(f"Test FAILED with status: {status}")
        print("Error message:", resp.json().get("error_message"))

if __name__ == "__main__":
    main()
