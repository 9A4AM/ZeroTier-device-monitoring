import requests
import configparser
import json
import os
from datetime import datetime, timezone, timedelta
import time
import pytz
import smtplib
from email.mime.text import MIMEText

LOG_FILE = 'zerotier_monitor.log'
STATUS_FILE = 'status.json'


config_path = os.path.join(os.path.dirname(__file__), 'config.ini')


def write_log(message):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a') as f:
        f.write(f"[{timestamp}] {message}\n")

# Load config
config = configparser.ConfigParser()
config.read('config.ini')

ZT_API_TOKEN = config['zerotier']['api_token']
ZT_NETWORK_ID = config['zerotier']['network_id']
# EMAIL_SENDER = config['gmail']['sender']
# EMAIL_PASS = config['gmail']['password']
# EMAIL_RECIPIENT = config['gmail']['recipient']
TZ_NAME = config['settings'].get('timezone', 'UTC')
USE_LOCAL_TIME = config['settings'].get('use_local_time', 'no').lower() == 'yes'
MONITORED_NAMES = [name.strip() for name in config['monitor']['members'].split(',')]
GRACE_MINUTES = int(config['settings'].get('online_grace_period_minutes', 5))
INTERVAL_MINUTES = int(config['settings'].get('check_interval_minutes', 10))

ZT_URL = f'https://api.zerotier.com/api/v1/network/{ZT_NETWORK_ID}/member'
HEADERS = {'Authorization': f'Bearer {ZT_API_TOKEN}'}
local_tz = pytz.timezone(TZ_NAME)

def load_status():
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_status(status):
    with open(STATUS_FILE, 'w') as f:
        json.dump(status, f)

def send_email(subject, body):
    method = config['email'].get('method', 'gmail').lower()

    msg = MIMEText(body)
    msg['Subject'] = subject

    if method == 'gmail':
        sender = config['gmail']['sender']
        password = config['gmail']['password']
        recipient = config['gmail']['recipient']
        smtp_server = 'smtp.gmail.com'
        smtp_port = 465

        msg['From'] = sender
        msg['To'] = recipient

        try:
            with smtplib.SMTP_SSL(smtp_server, smtp_port) as smtp:
                smtp.login(sender, password)
                smtp.send_message(msg)
            print(f"Email sent via Gmail: {subject}")
        except Exception as e:
            print(f"Error sending Gmail email: {e}")

    elif method == 'smtp':
        sender = config['smtp']['sender']
        password = config['smtp']['password']
        recipient = config['smtp']['recipient']
        smtp_server = config['smtp'].get('server')
        smtp_port = int(config['smtp'].get('port', 465))
        use_ssl = config['smtp'].get('use_ssl', 'yes').lower() == 'yes'

        msg['From'] = sender
        msg['To'] = recipient

        try:
            if use_ssl:
                with smtplib.SMTP_SSL(smtp_server, smtp_port) as smtp:
                    smtp.login(sender, password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(smtp_server, smtp_port) as smtp:
                    smtp.starttls()
                    smtp.login(sender, password)
                    smtp.send_message(msg)
            print(f"Email sent via SMTP: {subject}")
        except Exception as e:
            print(f"Error sending SMTP email: {e}")

    else:
        print(f"Unknown email method: {method}")


def format_time(epoch_ms):
    dt = datetime.fromtimestamp(int(epoch_ms) / 1000, tz=timezone.utc)
    if USE_LOCAL_TIME:
        dt = dt.astimezone(local_tz)
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def check_members():
    try:
        response = requests.get(ZT_URL, headers=HEADERS)
        response.raise_for_status()
        members = response.json()
    except Exception as e:
        print(f"Error fetching ZeroTier members: {e}")
        return

    previous_status = load_status()
    current_status = {}
    offline_alerts = []
    online_alerts = []

    print(f"{'NAME':20} {'ONLINE':8} {'LAST SEEN'}")
    print("-" * 50)

    now = datetime.utcnow()
    first_run = len(previous_status) == 0  # Check if it's the first run

    for member in members:
        node_id = member["nodeId"]
        name = member.get("name", node_id)
        if name not in MONITORED_NAMES:
            continue

        last_online_unix = member.get("lastOnline", 0)
        last_online_time = datetime.utcfromtimestamp(last_online_unix / 1000)
        last_seen_str = format_time(last_online_unix)

        online_flag = member.get("online", False)
        online = online_flag or (now - last_online_time) < timedelta(minutes=GRACE_MINUTES)

        was_online = previous_status.get(name, False)
        current_status[name] = online
        status_str = "YES" if online else "NO"

        # Skip sending email on the first run if the device is already online
        if first_run and online:
            continue  # Skip email if already online on first run

        if not online and was_online:
            msg = f"{name} became OFFLINE (Last seen: {last_seen_str})"
            offline_alerts.append(msg)
            write_log(msg)
        elif online and not was_online:
            msg = f"{name} is back ONLINE (Last seen: {last_seen_str})"
            online_alerts.append(msg)
            write_log(msg)

        print(f"{name:20} {status_str:8} {last_seen_str}")

    if offline_alerts:
        send_email("ZeroTier Offline Alert", "\n".join(offline_alerts))
    if online_alerts:
        send_email("ZeroTier Online Notice", "\n".join(online_alerts))

    save_status(current_status)

if __name__ == "__main__":
    try:
        while True:
            print(f"\n--- Checking ZeroTier status at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
            check_members()
            print(f"Next check in {INTERVAL_MINUTES} minutes...")
            time.sleep(INTERVAL_MINUTES * 60)
    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")
