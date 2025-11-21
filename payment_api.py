import os
import requests
import random

USE_LIVE_API = os.getenv('USE_LIVE_API', 'false').lower() == 'true'

def verify_remita_rrr(rrr):
    if not rrr:
        return {"verified": False, "amount": 0}

    if USE_LIVE_API:
        try:
            url = f"https://api.remita.net/payment-status/{rrr}"
            headers = {"Authorization": "Bearer YOUR_REMITA_API_KEY"}
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            if response.headers.get("Content-Type", "").startswith("application/json"):
                data = response.json()
            else:
                print("Unexpected content type from Remita:", response.headers.get("Content-Type"))
                data = {}

            return {
                "verified": data.get("status") == "SUCCESS",
                "amount": float(data.get("amount", 0))
            }

        except (requests.exceptions.RequestException, ValueError) as e:
            print(f"Remita API error: {e}")
            return {"verified": False, "amount": 0}

    else:
        # MOCKED response with randomized amount
        mock_amount = random.randint(10000, 200000)  # Simulate ₦10,000 to ₦200,000
        print(f"[MOCK] Verifying Remita RRR: {rrr} → ₦{mock_amount}")
        return {"verified": True, "amount": mock_amount}


def verify_paydirect_reference(reference):
    if not reference:
        return {"verified": False, "amount": 0}

    if USE_LIVE_API:
        try:
            url = f"https://interswitch.com/api/transaction-status/{reference}"
            headers = {"Authorization": "Bearer YOUR_INTERSWITCH_API_KEY"}
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            if response.headers.get("Content-Type", "").startswith("application/json"):
                data = response.json()
            else:
                print("Unexpected content type from PayDirect:", response.headers.get("Content-Type"))
                data = {}

            return {
                "verified": data.get("status") == "SUCCESS",
                "amount": float(data.get("amount", 0))
            }

        except (requests.exceptions.RequestException, ValueError) as e:
            print(f"PayDirect API error: {e}")
            return {"verified": False, "amount": 0}

    else:
        # MOCKED response with randomized amount
        mock_amount = random.randint(15500, 502000)  # Simulate ₦15,500 to ₦502,000
        print(f"[MOCK] Verifying PayDirect reference: {reference} → ₦{mock_amount}")
        return {"verified": True, "amount": mock_amount}

