import os
import re
import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict
from flask import Flask, render_template, request, redirect, url_for, session
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
# Generate a random secret key if not set, to allow sessions to work immediately
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24))

CONFIG_FILE = 'config.json'

def parse_jira_time(time_str):
    time_str = str(time_str).strip().lower()
    if not time_str:
        return 0
    
    pattern = r'(\d+(?:\.\d+)?)\s*([wdhm])'
    matches = re.findall(pattern, time_str)
    
    if not matches:
        try:
            return int(float(time_str) * 3600)
        except ValueError:
            return 0
            
    total_seconds = 0
    for val, unit in matches:
        val = float(val)
        if unit == 'w':
            total_seconds += val * 5 * 8 * 3600
        elif unit == 'd':
            total_seconds += val * 8 * 3600
        elif unit == 'h':
            total_seconds += val * 3600
        elif unit == 'm':
            total_seconds += val * 60
    return int(total_seconds)

def load_config():
    """Load configuration from file, falling back to environment variables."""
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
        except Exception as e:
            print(f"Error loading config file: {e}")
    
    final_config = {
        'JIRA_URL': config.get('JIRA_URL') or os.getenv('JIRA_URL'),
        'JIRA_EMAIL': config.get('JIRA_EMAIL') or os.getenv('JIRA_EMAIL'),
        'JIRA_API_TOKEN': config.get('JIRA_API_TOKEN') or os.getenv('JIRA_API_TOKEN')
    }
    return final_config

def save_config(jira_url, jira_email, jira_api_token):
    """Save configuration to file."""
    config = {
        'JIRA_URL': jira_url,
        'JIRA_EMAIL': jira_email,
        'JIRA_API_TOKEN': jira_api_token
    }
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        return True
    except Exception as e:
        print(f"Error saving config file: {e}")
        return False

def get_jira_config():
    # Helper to get config from session or file
    if session.get('JIRA_URL') and session.get('JIRA_EMAIL') and session.get('JIRA_API_TOKEN'):
        return {
            'JIRA_URL': session.get('JIRA_URL'),
            'JIRA_EMAIL': session.get('JIRA_EMAIL'),
            'JIRA_API_TOKEN': session.get('JIRA_API_TOKEN')
        }
    return load_config()

@app.route('/config', methods=['GET', 'POST'])
def config():
    if request.method == 'POST':
        jira_url = request.form['jira_url'].rstrip('/')
        jira_email = request.form['jira_email']
        jira_api_token = request.form['jira_api_token']
        
        save_config(jira_url, jira_email, jira_api_token)
        
        session['JIRA_URL'] = jira_url
        session['JIRA_EMAIL'] = jira_email
        session['JIRA_API_TOKEN'] = jira_api_token
        return redirect(url_for('index'))
    
    return render_template('config.html', config=get_jira_config())

def get_myself(auth, url):
    try:
        response = requests.get(f"{url}/rest/api/3/myself", auth=auth)
        if response.status_code == 401:
            return None
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching myself: {e}")
        return None

def get_worklogs_issues(auth, url, account_id, start_date, end_date):
    jql = f"worklogAuthor = '{account_id}' AND worklogDate >= '{start_date}' AND worklogDate <= '{end_date}'"
    search_url = f"{url}/rest/api/3/search/jql"
    params = {
        'jql': jql,
        'fields': 'summary', 
        'maxResults': 100
    }
    
    issues = []
    start_at = 0
    
    while True:
        params['startAt'] = start_at
        try:
            response = requests.get(search_url, auth=auth, params=params)
            response.raise_for_status()
            data = response.json()
            issues.extend(data.get('issues', []))
            
            if start_at + len(data.get('issues', [])) >= data.get('total', 0):
                break
            start_at += len(data.get('issues', []))
        except Exception as e:
            print(f"Error searching issues: {e}")
            break
            
    return issues

def get_latest_worklog_end_time(auth, jira_url, account_id, date_str):
    issues = get_worklogs_issues(auth, jira_url, account_id, date_str, date_str)
    
    max_end_time = None
    
    for issue in issues:
        worklog_url = f"{jira_url}/rest/api/3/issue/{issue['key']}/worklog"
        try:
            wl_resp = requests.get(worklog_url, auth=auth)
            if wl_resp.status_code == 200:
                worklogs = wl_resp.json().get('worklogs', [])
                for log in worklogs:
                    if log['author']['accountId'] == account_id:
                        started_str = log['started']
                        if started_str.startswith(date_str):
                            # Parse datetime with timezone
                            # Jira format: 2026-04-06T09:00:00.000+0000
                            dt = datetime.strptime(started_str, "%Y-%m-%dT%H:%M:%S.%f%z")
                            end_dt = dt + timedelta(seconds=log['timeSpentSeconds'])
                            if max_end_time is None or end_dt > max_end_time:
                                max_end_time = end_dt
        except Exception as e:
            continue
            
    return max_end_time

@app.route('/')
def index():
    config_data = get_jira_config()
    if not all(config_data.values()):
        return redirect(url_for('config'))

    auth = (config_data['JIRA_EMAIL'], config_data['JIRA_API_TOKEN'])
    jira_url = config_data['JIRA_URL']

    # Get date range from query parameters or default to last 30 days
    end_date_str = request.args.get('end_date', datetime.now().strftime('%Y-%m-%d'))
    start_date_str = request.args.get('start_date', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))

    myself = get_myself(auth, jira_url)
    if not myself:
        return render_template('index.html', time_data={}, error="Authentication failed. Please check your API Token and Email.", start_date=start_date_str, end_date=end_date_str)
    
    account_id = myself['accountId']
    issues = get_worklogs_issues(auth, jira_url, account_id, start_date_str, end_date_str)
    
    # Initialize all dates in range with 0 hours
    # Use regular dict instead of defaultdict because we are pre-filling keys
    time_data = {}
    
    current_date = datetime.strptime(start_date_str, '%Y-%m-%d')
    end_date_dt = datetime.strptime(end_date_str, '%Y-%m-%d') # Renamed to avoid confusion with parameter end_date
    
    while current_date <= end_date_dt:
        time_data[current_date.strftime('%Y-%m-%d')] = {
            'hours': 0.0, 
            'logs': [],
            'day_name': current_date.strftime('%A')
        }
        current_date += timedelta(days=1)
    
    # Track processed worklog IDs to avoid duplicates if specific worklogs are returned in multiple issue contexts (unlikely but safe)
    # Actually, we process by issue. One worklog belongs to one issue.
    
    for issue in issues:
        worklog_url = f"{jira_url}/rest/api/3/issue/{issue['key']}/worklog"
        try:
            wl_resp = requests.get(worklog_url, auth=auth)
            if wl_resp.status_code == 200:
                worklogs = wl_resp.json().get('worklogs', [])
                for log in worklogs:
                    if log['author']['accountId'] == account_id:
                        started = log['started'][:10] # YYYY-MM-DD
                        
                        # Only include if within selected range
                        if started >= start_date_str and started <= end_date_str:
                            time_spent_seconds = log['timeSpentSeconds']
                            time_spent_hours = time_spent_seconds / 3600.0
                            
                            # Handle comment safely
                            comment_raw = log.get('comment', '')
                            comment = ""
                            try:
                                if isinstance(comment_raw, str):
                                    comment = comment_raw
                                elif isinstance(comment_raw, dict):
                                    # deeply nested safely
                                    content = comment_raw.get('content', [])
                                    if content and len(content) > 0:
                                        inner_content = content[0].get('content', [])
                                        if inner_content and len(inner_content) > 0:
                                            comment = inner_content[0].get('text', '')
                            except:
                                comment = "Complex comment"

                            if started not in time_data:
                                time_data[started] = {
                                    'hours': 0.0, 
                                    'logs': [],
                                    'day_name': datetime.strptime(started, '%Y-%m-%d').strftime('%A')
                                }

                            time_data[started]['hours'] += time_spent_hours
                            time_data[started]['logs'].append({
                                'id': log['id'],
                                'key': issue['key'],
                                'summary': issue['fields']['summary'],
                                'time_spent_hours': time_spent_hours,
                                'comment': comment,
                                'url': f"{jira_url}/browse/{issue['key']}"
                            })
        except Exception as e:
            print(f"Error processing issue {issue['key']}: {e}")

    sorted_data = dict(sorted(time_data.items(), reverse=True))

    return render_template('index.html', time_data=sorted_data, start_date=start_date_str, end_date=end_date_str)

@app.route('/add_worklog', methods=['POST'])
def add_worklog():
    config_data = get_jira_config()
    if not all(config_data.values()):
        return redirect(url_for('config'))

    auth = (config_data['JIRA_EMAIL'], config_data['JIRA_API_TOKEN'])
    jira_url = config_data['JIRA_URL']
    
    issue_id = request.form.get('issue_id')
    comment = request.form.get('comment')
    
    start_date_str = request.form.get('start_date')
    end_date_str = request.form.get('end_date')

    if not issue_id:
        return redirect(url_for('index', start_date=start_date_str, end_date=end_date_str))
    
    myself = get_myself(auth, jira_url)
    if not myself:
        return redirect(url_for('index', start_date=start_date_str, end_date=end_date_str))
    account_id = myself['accountId']
    
    log_dates = request.form.getlist('log_date[]')
    log_times = request.form.getlist('log_time[]')
    
    date_latest_end = {}
    
    for date_str, time_str in zip(log_dates, log_times):
        if not date_str or not time_str:
            continue
            
        time_spent_seconds = parse_jira_time(time_str)
        if time_spent_seconds <= 0:
            continue
            
        try:
            if date_str not in date_latest_end:
                latest_end = get_latest_worklog_end_time(auth, jira_url, account_id, date_str)
                if latest_end is None:
                    # Default to 9:00 AM server timezone if no previous logs today
                    local_tz = datetime.now().astimezone().tzinfo
                    dt_unaware = datetime.strptime(f"{date_str} 09:00:00", "%Y-%m-%d %H:%M:%S")
                    latest_end = dt_unaware.replace(tzinfo=local_tz)
                date_latest_end[date_str] = latest_end
            
            started_dt = date_latest_end[date_str]
            # Jira payload expects format exactly like 2026-04-06T09:00:00.000+0000
            started = started_dt.strftime("%Y-%m-%dT%H:%M:%S.000%z")
            
            payload = {
                "timeSpentSeconds": time_spent_seconds,
                "started": started
            }
            
            if comment:
                payload["comment"] = {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": comment
                                }
                            ]
                        }
                    ]
                }
            
            url = f"{jira_url}/rest/api/3/issue/{issue_id}/worklog"
            response = requests.post(
                url, 
                json=payload, 
                auth=auth, 
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            
            # Successfully logged; update the date's latest end time so the next log sequences properly
            date_latest_end[date_str] = started_dt + timedelta(seconds=time_spent_seconds)
            
        except Exception as e:
            print(f"Error logging time for {date_str}: {e}")
    
    return redirect(url_for('index', start_date=start_date_str, end_date=end_date_str))

@app.route('/delete_worklog', methods=['POST'])
def delete_worklog():
    config_data = get_jira_config()
    if not all(config_data.values()):
        return redirect(url_for('config'))
        
    auth = (config_data['JIRA_EMAIL'], config_data['JIRA_API_TOKEN'])
    jira_url = config_data['JIRA_URL']
    
    issue_id = request.form.get('issue_id')
    worklog_id = request.form.get('worklog_id')
    start_date_str = request.form.get('start_date')
    end_date_str = request.form.get('end_date')
    
    if issue_id and worklog_id:
        try:
            url = f"{jira_url}/rest/api/3/issue/{issue_id}/worklog/{worklog_id}"
            response = requests.delete(url, auth=auth)
            response.raise_for_status()
        except Exception as e:
            print(f"Error deleting worklog {worklog_id} from issue {issue_id}: {e}")
            
    return redirect(url_for('index', start_date=start_date_str, end_date=end_date_str))

if __name__ == '__main__':
    print("Starting Jira Time Tracker...")
    print("Running on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)
