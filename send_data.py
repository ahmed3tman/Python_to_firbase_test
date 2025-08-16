import os
from pathlib import Path
import firebase_admin
from firebase_admin import credentials, db
import random
import time

"""
إرسال قراءات وهمية إلى Firebase Realtime Database.

الإعدادات:
- ملف اعتماد Firebase Admin يُستخرج من متغير بيئة FIREBASE_CREDENTIALS
    أو الافتراضي الموجود في نفس المجلد.
"""

# تحديد مسار ملف الاعتماد بشكل آمن حتى مع وجود مسافات في المسار
BASE_DIR = Path(__file__).resolve().parent

# اسم الملف الافتراضي الموجود لديك في المشروع
DEFAULT_CRED_FILE = "spider-doctor-firebase-adminsdk-fbsvc-fdea831f68.json"

# يمكن تجاوز المسار عبر متغير بيئة FIREBASE_CREDENTIALS
CRED_FILE = os.environ.get("FIREBASE_CREDENTIALS", DEFAULT_CRED_FILE)
CRED_PATH = (Path(CRED_FILE) if os.path.isabs(CRED_FILE) else BASE_DIR / CRED_FILE)

if not CRED_PATH.exists():
        raise FileNotFoundError(
                f"Firebase credentials file not found: {CRED_PATH}\n"
                "Tip: Put your service account JSON next to this script or set FIREBASE_CREDENTIALS to its absolute path."
        )

# تحميل بيانات الخدمة من Firebase
cred = credentials.Certificate(str(CRED_PATH))

# تهيئة الاتصال بـ Firebase
# تهيئة الاتصال بـ Firebase
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://spider-doctor-default-rtdb.firebaseio.com/'
    })

"""
منطقيّة القيم:
- بدون قفزات كبيرة: تغير تدريجي بسيط كل ثانية.
- نطاقات أقرب للواقع.
"""

# تعريف المسار ويمكن تغييره بمتغير بيئة DEVICE_ID
DEVICE_ID = os.environ.get("DEVICE_ID", "QW999")
base_path = f"devices/{DEVICE_ID}/readings"

_state = {
    # قيم أولية لشخص سليم مستريح
    "systolic": 118.0,
    "diastolic": 78.0,
    "heartRate": 74.0,
    "respiratoryRate": 15.0,
    "temperature": 36.8,
    "spo2": 98.0,
    "ecg": 74.0,  # سنجعلها الأسرع وتتبع النبض عن قرب
}

_last_update_t = time.monotonic()

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def _ou_step(name: str, lo: float, hi: float, mu: float, theta: float, sigma: float, dt: float) -> float:
    """
    عملية أورنشتاين-أولنبيك (انجراف نحو المتوسط + ضوضاء) لواقعية أعلى:
    x += theta*(mu - x)*dt + sigma*sqrt(dt)*N(0,1)
    - theta: سرعة الرجوع للمتوسط (الأكبر = أسرع تغير)
    - sigma: مقدار التذبذب العشوائي
    - dt: الفاصل الزمني الفعلي بالثواني
    """
    x = float(_state[name])
    # ضوضاء غاوسية بمتوسط صفر
    noise = random.gauss(0.0, 1.0)
    x += theta * (mu - x) * dt + sigma * (dt ** 0.5) * noise
    x = _clamp(x, lo, hi)
    _state[name] = x
    return x

def send_fake_data():
    global _last_update_t
    now = time.monotonic()
    dt = max(0.1, now - _last_update_t)  # حماية من dt=0

    # نبض القلب: تغيّر معتدل حول 75
    heart_rate = _ou_step(
        "heartRate", lo=60, hi=100, mu=75.0, theta=0.6, sigma=1.5, dt=dt
    )

    # ECG: الأسرع، يتبع النبض بسرعة مع ضوضاء أكبر قليلًا
    ecg_val = _ou_step(
        "ecg", lo=55, hi=110, mu=float(heart_rate), theta=1.8, sigma=3.0, dt=dt
    )

    # التنفّس: أبطأ قليلًا ويرتبط بشكل بسيط بالنبض
    rr_mu = _clamp(14.0 + (float(heart_rate) - 75.0) * 0.05, 12.0, 18.0)
    respiratory_rate = _ou_step(
        "respiratoryRate", lo=12, hi=20, mu=rr_mu, theta=0.35, sigma=0.25, dt=dt
    )

    # ضغط الدم: أبطأ ومستقر
    systolic = _ou_step(
        "systolic", lo=105, hi=130, mu=118.0, theta=0.18, sigma=0.7, dt=dt
    )
    diastolic = _ou_step(
        "diastolic", lo=65, hi=85, mu=78.0, theta=0.18, sigma=0.5, dt=dt
    )
    # منطق: الانبساطي أقل من الانقباضي بهامش معقول
    if diastolic > systolic - 25:
        diastolic = systolic - 25
        diastolic = _clamp(diastolic, 60, 90)
        _state["diastolic"] = diastolic

    # الحرارة: بطيئة جدًا ونطاق ضيق جدًا لشخص سليم
    temperature = _ou_step(
        "temperature", lo=36.5, hi=37.2, mu=36.8, theta=0.06, sigma=0.03, dt=dt
    )

    # SpO2: شبه ثابت
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
        # طباعة الخطأ والمتابعة في المرة القادمة
        print(f"Failed to send data: {e}")

def main():
    # تشغيل مستمر
    while True:
        send_fake_data()
    # 1 ثانية تمنح اختلافات طبيعية بين القياسات، مع كون ECG الأسرع
    time.sleep(1)

if __name__ == "__main__":
    main()
