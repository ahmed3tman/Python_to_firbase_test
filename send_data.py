import os
from pathlib import Path
import firebase_admin
from firebase_admin import credentials, db
import random
import time

"""Send fake readings to Firebase Realtime Database.

Configuration: credentials are loaded from the FIREBASE_CREDENTIALS
environment variable or the default JSON file next to this script.
"""

# Base directory for the script
BASE_DIR = Path(__file__).resolve().parent

# Default service account filename (updated to match attached file)
DEFAULT_CRED_FILE = "spider-doctor-firebase-adminsdk-fbsvc-aec2e00c74.json"

# Can be overridden by FIREBASE_CREDENTIALS env var
CRED_FILE = os.environ.get("FIREBASE_CREDENTIALS", DEFAULT_CRED_FILE)
CRED_PATH = (Path(CRED_FILE) if os.path.isabs(CRED_FILE) else BASE_DIR / CRED_FILE)

if not CRED_PATH.exists():
        raise FileNotFoundError(
                f"Firebase credentials file not found: {CRED_PATH}\n"
                "Tip: Put your service account JSON next to this script or set FIREBASE_CREDENTIALS to its absolute path."
        )

# Load service account credentials
cred = credentials.Certificate(str(CRED_PATH))

# Initialize Firebase app
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://spider-doctor-default-rtdb.firebaseio.com/'
    })

"""Value model:
- small gradual changes each second
- realistic physiological ranges
"""

# Device ID and database path (can be overridden by DEVICE_ID env var)
DEVICE_ID = os.environ.get("DEVICE_ID", "QW1234")
base_path = f"devices/{DEVICE_ID}/readings"

_state = {
    # initial values for a resting healthy person
    "systolic": 118.0,
    "diastolic": 78.0,
    "heartRate": 74.0,
    "respiratoryRate": 15.0,
    "temperature": 36.8,
    "spo2": 98.0,
    "ecg": 74.0,  # ECG follows heart rate closely
}

_last_update_t = time.monotonic()

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def _ou_step(name: str, lo: float, hi: float, mu: float, theta: float, sigma: float, dt: float) -> float:
    """Ornstein-Uhlenbeck step: drift towards mean + gaussian noise.

    x += theta * (mu - x) * dt + sigma * sqrt(dt) * N(0,1)
    - theta: rate of mean reversion
    - sigma: noise magnitude
    - dt: time delta in seconds
    """
    x = float(_state[name])
    # gaussian noise (mean 0)
    noise = random.gauss(0.0, 1.0)
    x += theta * (mu - x) * dt + sigma * (dt ** 0.5) * noise
    x = _clamp(x, lo, hi)
    _state[name] = x
    return x

def send_fake_data():
    global _last_update_t
    now = time.monotonic()
    dt = max(0.1, now - _last_update_t)  # protect against dt=0

    # heart rate: moderate variation around 75
    heart_rate = _ou_step(
        "heartRate", lo=60, hi=100, mu=75.0, theta=0.6, sigma=1.5, dt=dt
    )

    # ECG: faster, follows heart rate with slightly larger noise
    ecg_val = _ou_step(
        "ecg", lo=55, hi=110, mu=float(heart_rate), theta=1.8, sigma=3.0, dt=dt
    )

    # respiratory rate: slower, loosely correlated with heart rate
    rr_mu = _clamp(14.0 + (float(heart_rate) - 75.0) * 0.05, 12.0, 18.0)
    respiratory_rate = _ou_step(
        "respiratoryRate", lo=12, hi=20, mu=rr_mu, theta=0.35, sigma=0.25, dt=dt
    )

    # blood pressure: slower and more stable
    systolic = _ou_step(
        "systolic", lo=105, hi=130, mu=118.0, theta=0.18, sigma=0.7, dt=dt
    )
    diastolic = _ou_step(
        "diastolic", lo=65, hi=85, mu=78.0, theta=0.18, sigma=0.5, dt=dt
    )
    # ensure diastolic is reasonably lower than systolic
    if diastolic > systolic - 25:
        diastolic = systolic - 25
        diastolic = _clamp(diastolic, 60, 90)
        _state["diastolic"] = diastolic

    # temperature: very slow changes, narrow range
    temperature = _ou_step(
        "temperature", lo=36.5, hi=37.2, mu=36.8, theta=0.06, sigma=0.03, dt=dt
    )

    # SpO2: nearly constant
    spo2 = _ou_step("spo2", lo=96, hi=100, mu=98.5, theta=0.25, sigma=0.12, dt=dt)

    _last_update_t = now

    timestamp = int(time.time())  # وقت Unix

    data = {
        "bloodPressure": {
            "systolic": int(round(systolic)),
            "diastolic": int(round(diastolic)),
        },
        "heartRate": int(round(heart_rate)),
        "respiratoryRate": int(round(respiratory_rate)),
        "temperature": round(float(temperature), 1),
        "spo2": int(round(spo2)),
        "ecg": int(round(ecg_val)),
        "lastUpdated": timestamp,
    }

    try:
        ref = db.reference(base_path)
        ref.set(data)
        print(f"Data sent at {timestamp}: {data}")
    except Exception as e:
        # log error and continue
        print(f"Failed to send data: {e}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Send fake readings to Firebase')
    parser.add_argument('--validate', action='store_true', help='Validate credentials file exists')
    parser.add_argument('--once', action='store_true', help='Send a single data sample and exit')
    args = parser.parse_args()

    if args.validate:
        print(f"Credential file: {CRED_PATH} -> exists: {CRED_PATH.exists()}")
        return

    if args.once:
        send_fake_data()
        return

    # continuous run: send one sample per second
    try:
        while True:
            send_fake_data()
            # 1 second pause to allow natural variation
            time.sleep(1)
    except KeyboardInterrupt:
        print('\nInterrupted; exiting')

if __name__ == "__main__":
    main()
