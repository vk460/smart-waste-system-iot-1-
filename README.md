# EcoReward Django

EcoReward is a Django web application for an IoT recycling reward machine. It includes a user desktop/mobile-responsive experience, an admin dashboard, SQLite data storage, central reward calculation, and authenticated machine endpoints.

## Run

Use Python 3.9+ and install dependencies:

```powershell
py -m pip install -r requirements.txt
py manage.py migrate
py seed_demo.py
py manage.py runserver
```

Open `http://127.0.0.1:8000/`.

Demo accounts:

- User: `prajwal` / `ecoreward123`
- Admin: `admin` / `admin123`

## Hardware API

The ESP8266 must call the backend, never the browser directly. Every request uses JSON and the machine `api_key`.

```http
POST /api/machines/rfid-scan/
{"machine_id":"MACHINE_001","api_key":"change-me","rfid_uid":"A3:B7:91:24"}
```

Then send the measured weight:

```http
POST /api/machines/weight/
{"machine_id":"MACHINE_001","api_key":"change-me","session_id":1,"weight":18.4}
```

Finally report the physical bin movement:

```http
POST /api/machines/session-complete/
{"machine_id":"MACHINE_001","api_key":"change-me","session_id":1}
```

Heartbeat and fill level:

```http
POST /api/machines/heartbeat/
{"machine_id":"MACHINE_001","api_key":"change-me","bin_level":62}
```

The backend owns RFID verification, session states, weight validation, points, user balances, notifications, history, and admin reporting. Replace `change-me` with a per-machine secret before hardware deployment and place the service behind HTTPS.

## Structure

- `recycling/models.py`: shared users, machines, sessions, rewards, notifications
- `recycling/views.py`: user/admin views and hardware API state machine
- `templates/user/`: responsive user UI
- `templates/admin/`: admin UI
- `static/css/app.css`: shared EcoReward theme
