import os
import json
import base64
import tempfile
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'takamori-calendar-secret-2026')

_tmp = tempfile.gettempdir()
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), 'credentials.json')
TOKEN_FILE = os.path.join(os.path.dirname(__file__), 'token.json')

# Render環境：環境変数からJSONファイルを /tmp に生成
_creds_env = os.environ.get('GOOGLE_CREDENTIALS_JSON')
if _creds_env:
    CREDENTIALS_FILE = os.path.join(_tmp, 'credentials.json')
    with open(CREDENTIALS_FILE, 'w') as _f:
        _f.write(_creds_env)

_token_env = os.environ.get('GOOGLE_TOKEN_JSON')
if _token_env:
    TOKEN_FILE = os.path.join(_tmp, 'token.json')
    with open(TOKEN_FILE, 'w') as _f:
        _f.write(_token_env)
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/gmail.send',
]
SLOT_MINUTES = 30
DISPLAY_DAYS = 14
BUSINESS_START = 9
BUSINESS_END = 22
TIMEZONE = 'Asia/Tokyo'
OWNER_NAME = '高森 雄大'
OWNER_EMAIL = 'k.takamori19860514@gmail.com'

WEEKDAY_JA = ['月', '火', '水', '木', '金', '土', '日']


def get_service():
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if creds and creds.valid:
            return build('calendar', 'v3', credentials=creds)
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            with open(TOKEN_FILE, 'w') as f:
                f.write(creds.to_json())
            return build('calendar', 'v3', credentials=creds)
    except Exception:
        pass
    return None


def _build_gmail_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
    return build('gmail', 'v1', credentials=creds)


def _send_mail(gmail, to, subject, body):
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['To'] = to
    msg['From'] = OWNER_EMAIL
    msg['Subject'] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    gmail.users().messages().send(userId='me', body={'raw': raw}).execute()


def _format_time_range(start_iso, end_iso):
    tz = ZoneInfo(TIMEZONE)
    start_dt = datetime.fromisoformat(start_iso).astimezone(tz)
    end_dt = datetime.fromisoformat(end_iso).astimezone(tz)
    wd = WEEKDAY_JA[start_dt.weekday()]
    return start_dt.strftime(f'%m/%d({wd}) %H:%M') + '〜' + end_dt.strftime('%H:%M')


def send_notification_email(name, email, purpose, start_iso, end_iso, meet_url=None):
    try:
        if not os.path.exists(TOKEN_FILE):
            print('[通知] token.jsonが見つかりません')
            return
        gmail = _build_gmail_service()
        time_range = _format_time_range(start_iso, end_iso)
        meeting_line = f'形式：オンライン（Google Meet）\nMeetリンク：{meet_url}\n' if meet_url else '形式：対面\n'

        subject = f'【予約】{name} さん｜{time_range}'
        body = (
            f'新しい予約が入りました。\n\n'
            f'日時：{time_range}\n'
            f'{meeting_line}'
            f'氏名：{name}\n'
            f'メール：{email}\n'
            f'用件：{purpose or "（未記入）"}\n\n'
            f'Googleカレンダーに自動登録済みです。'
        )
        _send_mail(gmail, OWNER_EMAIL, subject, body)
        print(f'[通知] メール送信成功: {subject}')
    except Exception as e:
        print(f'[通知] メール送信エラー: {e}')


def send_confirmation_email(name, email, purpose, start_iso, end_iso, meet_url=None):
    try:
        if not os.path.exists(TOKEN_FILE):
            print('[確認メール] token.jsonが見つかりません')
            return
        gmail = _build_gmail_service()
        time_range = _format_time_range(start_iso, end_iso)
        meeting_line = f'形式：オンライン（Google Meet）\nMeetリンク：{meet_url}\n' if meet_url else '形式：対面\n'

        subject = f'【日程確定】{time_range}｜{OWNER_NAME}'
        body = (
            f'{name} 様\n\n'
            f'日程調整にご協力いただきありがとうございます。\n'
            f'下記の日程で確定しましたのでお知らせいたします。\n\n'
            f'日時：{time_range}\n'
            f'{meeting_line}'
            f'担当：{OWNER_NAME}\n\n'
            f'ご不明な点がございましたら、このメールにご返信ください。\n\n'
            f'{OWNER_NAME}'
        )
        _send_mail(gmail, email, subject, body)
        print(f'[確認メール] 送信成功: {email}')
    except Exception as e:
        print(f'[確認メール] 送信エラー: {e}')


def parse_dt(s):
    dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    return dt.astimezone(ZoneInfo(TIMEZONE))


def get_free_slots(service, calendar_ids=None, days=DISPLAY_DAYS):
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days)).isoformat()

    if calendar_ids is None:
        calendar_ids = ['primary']

    body = {
        'timeMin': time_min,
        'timeMax': time_max,
        'timeZone': TIMEZONE,
        'items': [{'id': cid} for cid in calendar_ids],
    }
    result = service.freebusy().query(body=body).execute()

    all_busy = []
    for cid in calendar_ids:
        for b in result['calendars'].get(cid, {}).get('busy', []):
            all_busy.append((parse_dt(b['start']), parse_dt(b['end'])))

    all_busy.sort(key=lambda x: x[0])
    merged = []
    for start, end in all_busy:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    slots = []
    current = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    end_date = now + timedelta(days=days)

    while current < end_date:
        if current.weekday() >= 5:
            current = (current + timedelta(days=1)).replace(
                hour=BUSINESS_START, minute=0, second=0, microsecond=0)
            continue
        if current.hour < BUSINESS_START:
            current = current.replace(hour=BUSINESS_START, minute=0)
            continue
        if current.hour >= BUSINESS_END:
            current = (current + timedelta(days=1)).replace(
                hour=BUSINESS_START, minute=0, second=0, microsecond=0)
            continue

        slot_end = current + timedelta(minutes=SLOT_MINUTES)
        is_free = all(
            not (current < b_end and slot_end > b_start)
            for b_start, b_end in merged
        )

        if is_free:
            wd = WEEKDAY_JA[current.weekday()]
            slots.append({
                'start': current.isoformat(),
                'end': slot_end.isoformat(),
                'label': current.strftime(f'%m/%d({wd}) %H:%M'),
                'date': current.strftime('%Y-%m-%d'),
                'date_label': current.strftime(f'%m月%d日({wd})'),
                'time': current.strftime('%H:%M'),
            })

        current += timedelta(minutes=SLOT_MINUTES)

    return slots


@app.route('/')
def index():
    authenticated = get_service() is not None
    return render_template('index.html', authenticated=authenticated)


@app.route('/auth/login')
def auth_login():
    flow = Flow.from_client_secrets_file(CREDENTIALS_FILE, scopes=SCOPES)
    flow.redirect_uri = url_for('callback', _external=True)
    auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
    session['state'] = state
    # flow.code_verifier に PKCE の検証値が格納される（autogenerate_code_verifier=True）
    if flow.code_verifier:
        session['code_verifier'] = flow.code_verifier
    return redirect(auth_url)


@app.route('/callback')
def callback():
    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE, scopes=SCOPES, state=session.get('state'))
    flow.redirect_uri = url_for('callback', _external=True)
    # code_verifier を復元してトークン交換に含める
    cv = session.get('code_verifier')
    if cv:
        flow.code_verifier = cv
    flow.fetch_token(authorization_response=request.url)
    with open(TOKEN_FILE, 'w') as f:
        f.write(flow.credentials.to_json())
    return redirect(url_for('index'))


@app.route('/booking')
def booking():
    service = get_service()
    if not service:
        return render_template('booking.html', grouped={}, owner=OWNER_NAME, error='カレンダーに接続できませんでした。', hide_nav=True)
    try:
        slots = get_free_slots(service)
        grouped = {}
        for s in slots:
            d = s['date']
            if d not in grouped:
                grouped[d] = {'label': s['date_label'], 'slots': []}
            grouped[d]['slots'].append(s)
        return render_template('booking.html', grouped=grouped, owner=OWNER_NAME, error=None, hide_nav=True)
    except Exception as e:
        return render_template('booking.html', grouped={}, owner=OWNER_NAME, error=str(e), hide_nav=True)


@app.route('/api/book', methods=['POST'])
def api_book():
    service = get_service()
    if not service:
        return jsonify({'error': '認証が必要です'}), 401

    data = request.get_json()
    start = data.get('start', '').strip()
    end = data.get('end', '').strip()
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    purpose = data.get('purpose', '').strip()
    is_online = data.get('meeting_type') == 'online'

    if not all([start, end, name, email]):
        return jsonify({'error': '必須項目が不足しています'}), 400

    event = {
        'summary': f'[予約] {name} さんとのMTG',
        'description': f'目的: {purpose}\n予約者: {name} ({email})',
        'start': {'dateTime': start, 'timeZone': TIMEZONE},
        'end': {'dateTime': end, 'timeZone': TIMEZONE},
        'attendees': [{'email': OWNER_EMAIL}, {'email': email}],
        'reminders': {'useDefault': True},
    }
    if is_online:
        import uuid
        event['conferenceData'] = {
            'createRequest': {
                'requestId': str(uuid.uuid4()),
                'conferenceSolutionKey': {'type': 'hangoutsMeet'},
            }
        }

    try:
        created = service.events().insert(
            calendarId='primary', body=event, sendUpdates='all',
            conferenceDataVersion=1 if is_online else 0).execute()

        meet_url = None
        if is_online:
            for ep in created.get('conferenceData', {}).get('entryPoints', []):
                if ep.get('entryPointType') == 'video':
                    meet_url = ep.get('uri')
                    break

        send_notification_email(name, email, purpose, start, end, meet_url)
        send_confirmation_email(name, email, purpose, start, end, meet_url)
        return jsonify({'success': True, 'event_id': created['id']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/team')
def team():
    authenticated = get_service() is not None
    return render_template('team.html', authenticated=authenticated)


@app.route('/api/team-slots', methods=['POST'])
def api_team_slots():
    service = get_service()
    if not service:
        return jsonify({'error': '認証が必要です'}), 401

    data = request.get_json()
    raw_ids = data.get('calendar_ids', '')
    days = int(data.get('days', 7))

    calendar_ids = ['primary']
    for cid in raw_ids.split('\n'):
        cid = cid.strip()
        if cid and cid not in calendar_ids:
            calendar_ids.append(cid)

    try:
        slots = get_free_slots(service, calendar_ids=calendar_ids, days=days)
        # 候補を LINE/Slack に貼れる形式にも変換
        text_lines = []
        prev_date = None
        for s in slots[:20]:
            if s['date'] != prev_date:
                text_lines.append(f"\n■ {s['date_label']}")
                prev_date = s['date']
            text_lines.append(f"  ・{s['time']}〜")
        copy_text = '\n'.join(text_lines).strip()

        return jsonify({'slots': slots[:40], 'copy_text': copy_text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.run(debug=True, port=5000)
