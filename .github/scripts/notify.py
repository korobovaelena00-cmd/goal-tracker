#!/usr/bin/env python3
"""
Morning reminder script for Goal Tracker.
Runs via GitHub Actions at 6:00 MSK daily.
Reads user-data.json from repo, formats HTML message, sends via Telegram Bot API.
On Mondays, includes a weekly report.
"""
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

# Timezone: Moscow
MSK = timezone(timedelta(hours=3))
now = datetime.now(MSK)
today = now.strftime('%Y-%m-%d')
is_monday = now.weekday() == 0  # Monday=0

print(f"Running at {now.isoformat()}, today={today}, is_monday={is_monday}")

# Load user data
data_path = "user-data.json"
if not os.path.exists(data_path):
    print("ERROR: user-data.json not found")
    sys.exit(1)

with open(data_path, "r", encoding="utf-8") as f:
    state = json.load(f)

# --- Helper functions (mirror the app's logic) ---

DOW_RU = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']
DOW_RU_SHORT = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб']
MONTHS_RU = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']

def date_str(d):
    return d.strftime('%Y-%m-%d')

def is_habit_active(h):
    end_date = h.get('endDate')
    if end_date and end_date != '':
        try:
            if datetime.fromisoformat(end_date) < datetime.now():
                return False
        except:
            pass
    start_date = h.get('startDate')
    if start_date and start_date != '':
        try:
            if datetime.fromisoformat(start_date) > datetime.now():
                return False
        except:
            pass
    return True

def is_habit_scheduled_for_date(h, ds):
    dow_list = h.get('daysOfWeek', [])
    if not dow_list:
        return False
    dt = datetime.fromisoformat(ds)
    return dt.weekday() == 0 and 1 in dow_list or dow_list.count(dt.weekday()) > 0

# Actually JS getDay(): 0=Sunday, 1=Monday, ..., 6=Saturday
# Python weekday(): 0=Monday, ..., 6=Sunday
# So JS getDay() = (Python weekday() + 1) % 7
def js_getday(d):
    return (d.weekday() + 1) % 7

def is_habit_scheduled(h, ds):
    dow_list = h.get('daysOfWeek', [])
    if not dow_list:
        return False
    dt = datetime.fromisoformat(ds)
    return js_getday(dt) in dow_list

def calc_goal_progress(g):
    subs = g.get('subtasks', [])
    if not subs:
        return g.get('progress', 0)
    done = sum(1 for s in subs if s.get('done'))
    return round(done / len(subs) * 100)

def get_phrase_of_day(ds):
    phrases = [
        {"text": "Делай сегодня то, что другие не хотят, завтра будешь жить так, как другие не могут", "author": "народная мудрость"},
        {"text": "Маленькие шаги приводят к большим переменам", "author": "китайская поговорка"},
        {"text": "Лучшее время начать было вчера. Второе лучшее — сегодня", "author": "народная мудрость"},
        {"text": "Не жди момента. Момент это сейчас", "author": "народная мудрость"},
        {"text": "Путь длиной в тысячу ли начинается с первого шага", "author": "Лао-Цзы"},
        {"text": "Каждый день — это новая возможность стать лучше, чем вчера", "author": "народная мудрость"},
        {"text": "Успех — это сумма маленьких усилий, повторяемых изо дня в день", "author": "Роберт Колье"},
        {"text": "Не считай дни, а делай дни счастливыми", "author": "народная мудрость"},
        {"text": "Дисциплина — это мост между целями и достижениями", "author": "Джим Рохн"},
        {"text": "Падая семь раз, встань восемь", "author": "народная мудрость"},
        {"text": "Единственный способ делать великие дела — любить то, что делаешь", "author": "Стив Джобс"},
        {"text": "Привычка — это нить, из которой ткачется ткань характера", "author": "народная мудрость"},
        {"text": "Человек, который движется вперёд, никогда не возвращается назад", "author": "народная мудрость"},
        {"text": "Мотивация приходит с действием, а не до него", "author": "народная мудрость"},
        {"text": "Завтрашний день начинается сегодняшних решений", "author": "народная мудрость"},
        {"text": "Тебе не нужно быть великим, чтобы начать, но тебе нужно начать, чтобы стать великим", "author": "Зиг Зиглар"},
        {"text": "Меня не победить. Можно только проиграть собственную лень", "author": "народная мудрость"},
        {"text": "Прогресс — это не скорость, а направление", "author": "народная мудрость"},
        {"text": "Лучше медленно, чем никак", "author": "народная мудрость"},
        {"text": "Вечная привычка не формируется за день. Она формируется ежедневно", "author": "народная мудрость"},
        {"text": "Побеждает тот, кто верит в победу", "author": "народная мудрость"},
        {"text": "Невозможное — это мнение, а не факт", "author": "народная мудрость"},
        {"text": "Каждая привычка когда-то была впервые", "author": "народная мудрость"},
        {"text": "Терпение — горькое растение, но плод его сладок", "author": "народная мудрость"},
        {"text": "Делай или не делай. Не пытайся", "author": "Йода"},
        {"text": "Самый длинный путь начинается с первого шага", "author": "Лао-Цзы"},
        {"text": "Не откладывай на завтра то, что можно сделать сегодня", "author": "народная мудрость"},
        {"text": "Учись так, словно будешь жить вечно. Живи так, словно умрёшь завтра", "author": "Махатма Ганди"},
        {"text": "Победа над собой — первая и главная победа", "author": "народная мудрость"},
        {"text": "Кто рано встаёт, тому Бог подаёт", "author": "народная мудрость"},
    ]
    dt = datetime.fromisoformat(ds) if ds else datetime.now(MSK)
    day_of_year = (dt - datetime(dt.year, 1, 1)).days + 1
    p = phrases[day_of_year % len(phrases)]
    return p

def escape_html(s):
    return str(s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

# --- Build morning message ---

def build_morning_message(state):
    today_dt = datetime.now(MSK)
    dow_name = DOW_RU[today_dt.weekday()]
    date_display = f"{today_dt.day} {MONTHS_RU[today_dt.month - 1]}"
    
    habits = state.get('habits', [])
    goals = state.get('goals', [])
    tasks = state.get('tasks', [])
    
    # Today's scheduled habits
    scheduled_habits = []
    for h in habits:
        if not is_habit_active(h):
            continue
        if is_habit_scheduled(h, today):
            scheduled_habits.append(h)
    
    # Today's tasks
    todays_tasks = [t for t in tasks if t.get('date') == today and not t.get('done')]
    
    # Subtasks due today
    subtasks_due = []
    for g in goals:
        for s in g.get('subtasks', []):
            if s.get('dueDate') == today and not s.get('done'):
                subtasks_due.append({'goal_title': g.get('title', ''), 'subtask_text': s.get('text', '')})
    
    # Goals with deadline today
    goals_due = [g for g in goals if g.get('targetDate') == today and calc_goal_progress(g) < 100]
    
    # Phrase of day
    phrase = get_phrase_of_day(today)
    
    # Build HTML
    parts = []
    
    # Header
    parts.append(f"<b>Доброе утро!</b>")
    parts.append(f"<i>{dow_name}, {date_display}</i>")
    parts.append("")
    
    # Habits
    if scheduled_habits:
        parts.append("<b>Привычки на сегодня:</b>")
        for h in scheduled_habits:
            parts.append(f"  — {escape_html(h.get('title', ''))}")
        parts.append("")
    else:
        parts.append("<i>На сегодня нет запланированных привычек</i>")
        parts.append("")
    
    # Tasks
    if todays_tasks:
        parts.append("<b>Задачи на сегодня:</b>")
        for t in todays_tasks:
            parts.append(f"  — {escape_html(t.get('title', ''))}")
        parts.append("")
    
    # Subtasks
    if subtasks_due:
        parts.append("<b>Подзадачи из целей:</b>")
        for st in subtasks_due:
            parts.append(f"  — {escape_html(st['subtask_text'])} <i>({escape_html(st['goal_title'])})</i>")
        parts.append("")
    
    # Goals due
    if goals_due:
        parts.append("<b>Дедлайны целей сегодня:</b>")
        for g in goals_due:
            parts.append(f"  — {escape_html(g.get('title', ''))} ({calc_goal_progress(g)}%)")
        parts.append("")
    
    # Phrase of day
    parts.append(f"<b>Мотивация дня:</b>")
    parts.append(f"<i>\"{escape_html(phrase['text'])}\"</i>")
    if phrase.get('author'):
        parts.append(f"<i>— {escape_html(phrase['author'])}</i>")
    parts.append("")
    
    parts.append("<i>Откройте приложение, чтобы отметить выполнение</i>")
    
    return "\n".join(parts)


def build_weekly_report(state):
    """Build Monday weekly report."""
    today_dt = datetime.now(MSK)
    
    # Week start (Monday)
    days_since_monday = today_dt.weekday()
    week_start = today_dt - timedelta(days=days_since_monday)
    week_end = week_start + timedelta(days=6)
    prev_week_start = week_start - timedelta(days=7)
    
    habits = state.get('habits', [])
    goals = state.get('goals', [])
    tasks = state.get('tasks', [])
    
    # Calculate stats for this week and previous week
    def week_stats(ws):
        we = ws + timedelta(days=6)
        habit_done = 0
        habit_scheduled = 0
        for h in habits:
            if not is_habit_active(h):
                continue
            for i in range(7):
                d = ws + timedelta(days=i)
                ds = date_str(d)
                if is_habit_scheduled(h, ds):
                    habit_scheduled += 1
                    if ds in h.get('checkoffs', []):
                        habit_done += 1
        
        task_total = 0
        task_done = 0
        for t in tasks:
            tdate = t.get('date', '')
            if ws.isoformat()[:10] <= tdate <= we.isoformat()[:10]:
                task_total += 1
                if t.get('done'):
                    task_done += 1
        
        return {'habit_done': habit_done, 'habit_scheduled': habit_scheduled,
                'task_done': task_done, 'task_total': task_total}
    
    curr = week_stats(week_start)
    prev = week_stats(prev_week_start)
    
    # Habit success rates
    habit_rates = []
    for h in habits:
        if not is_habit_active(h):
            continue
        sched = 0
        done = 0
        for i in range(7):
            d = week_start + timedelta(days=i)
            ds = date_str(d)
            if is_habit_scheduled(h, ds):
                sched += 1
                if ds in h.get('checkoffs', []):
                    done += 1
        if sched > 0:
            habit_rates.append({'title': h.get('title', ''), 'done': done, 'scheduled': sched, 'rate': round(done/sched*100)})
    habit_rates.sort(key=lambda x: x['rate'], reverse=True)
    
    # Goals completed this week
    goals_completed = []
    for g in goals:
        completed_at = g.get('completedAt', '')
        if completed_at:
            try:
                ca = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
                if week_start <= ca <= week_end + timedelta(days=1):
                    goals_completed.append(g)
            except:
                pass
    
    # Active goals
    active_goals = [g for g in goals if calc_goal_progress(g) < 100]
    
    parts = []
    parts.append("<b>Еженедельный отчёт</b>")
    parts.append(f"<i>{week_start.day}—{week_end.day} {MONTHS_RU[week_end.month - 1]}</i>")
    parts.append("")
    
    # Overview stats
    parts.append("<b>Итоги недели:</b>")
    
    habit_rate = round(curr['habit_done'] / curr['habit_scheduled'] * 100) if curr['habit_scheduled'] > 0 else 0
    prev_habit_rate = round(prev['habit_done'] / prev['habit_scheduled'] * 100) if prev['habit_scheduled'] > 0 else 0
    delta = habit_rate - prev_habit_rate
    delta_str = f" (+{delta}%)" if delta > 0 else (f" ({delta}%)" if delta < 0 else "")
    
    parts.append(f"  Привычки: <b>{habit_rate}%</b> ({curr['habit_done']}/{curr['habit_scheduled']}){delta_str}")
    parts.append(f"  Задачи: <b>{curr['task_done']}/{curr['task_total']}</b>")
    parts.append(f"  Целей завершено: <b>{len(goals_completed)}</b>")
    parts.append("")
    
    # Best/worst habits
    if habit_rates:
        parts.append("<b>Привычки:</b>")
        for hr in habit_rates[:5]:
            bar = "█" * round(hr['rate'] / 10) + "░" * (10 - round(hr['rate'] / 10))
            parts.append(f"  {bar} {escape_html(hr['title'])} — {hr['rate']}% ({hr['done']}/{hr['scheduled']})")
        parts.append("")
    
    # Active goals
    if active_goals:
        parts.append("<b>Активные цели:</b>")
        for g in active_goals[:5]:
            parts.append(f"  — {escape_html(g.get('title', ''))} ({calc_goal_progress(g)}%)")
        parts.append("")
    
    # Reflection
    parts.append("<i>Откройте приложение → Ревью для рефлексии</i>")
    
    return "\n".join(parts)


# --- Send via Telegram ---

def send_telegram(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    
    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        sys.exit(1)
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True
    }).encode('utf-8')
    
    req = urllib.request.Request(url, data=payload, headers={
        'Content-Type': 'application/json'
    })
    
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read().decode('utf-8'))
        if result.get('ok'):
            print("Message sent successfully!")
            print(f"Message ID: {result.get('result', {}).get('message_id')}")
        else:
            print(f"Telegram API error: {result}")
            sys.exit(1)
    except Exception as e:
        print(f"Error sending message: {e}")
        sys.exit(1)


# --- Main ---

if __name__ == '__main__':
    # Build morning message
    msg = build_morning_message(state)
    print(f"\n--- Morning message ---\n{msg}\n")
    send_telegram(msg)
    
    # Monday: also send weekly report
    if is_monday:
        print("\n--- Sending weekly report (Monday) ---")
        report = build_weekly_report(state)
        print(f"\n{report}\n")
        send_telegram(report)
    
    print("\nDone!")
