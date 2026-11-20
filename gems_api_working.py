#!/usr/bin/env python3
"""
WORKING GEMS API - Based on actual HAR file analysis
Uses the REAL endpoints discovered from the official GEMS app
"""

import requests
import json
import re
import ast  # For parsing Python-style dictionaries
import unicodedata
from datetime import datetime, timedelta, timezone

def has_control_characters(text):
    """
    Check if text contains problematic control characters.
    Returns True if control characters are found (needed for some 3rd year students).
    """
    for char in text:
        code = ord(char)
        # Check for control characters except tab, newline, carriage return
        if code < 32 and char not in '\t\n\r':
            return True
    return False

def sanitize_json_text(text):
    """
    Remove control characters that break JSON parsing.
    Only removes ASCII control characters (0x00-0x1F) except common whitespace.
    Preserves Unicode characters and normal content.
    
    Note: This is only needed for some 3rd year students whose data contains
    control characters in the GEMS API response.
    """
    # Remove only ASCII control characters, keeping tab(\t), newline(\n), carriage return(\r)
    # These are: 0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F, 0x7F
    cleaned = []
    for char in text:
        code = ord(char)
        # Keep if it's not a control character, OR if it's tab/newline/carriage return
        if code >= 32 or char in '\t\n\r':
            cleaned.append(char)
        else:
            # Replace control char with space
            cleaned.append(' ')
    return ''.join(cleaned)

def time_to_24h(t):
    """
    Convert GEMS '9:00 AM' style times to 'HH:MM:SS'.
    Note: GEMS labels the 12 noon slot as '12:00 AM' — no college class runs
    at midnight, so hour 0 is treated as noon.
    """
    t = str(t).strip()
    for fmt in ('%I:%M %p', '%I:%M%p'):
        try:
            parsed = datetime.strptime(t.upper(), fmt)
            if parsed.hour == 0:
                parsed = parsed.replace(hour=12)
            return parsed.strftime('%H:%M:%S')
        except ValueError:
            continue
    return t

def parse_timetable_js_text(text):
    """
    Robust parser for the malformed JS-style timetable response from GEMS.
    The raw payload mixes `key='value'` and `key :'value'` syntax which breaks
    both ast.literal_eval and json.loads, so instead of fixing the whole
    document we extract each day block and pull out quoted fields directly.

    Returns {'Monday': [{time, subjectName, code, facultyName, roomNo}], ...}
    or None if nothing could be extracted.
    """
    compact = re.sub(r'\s+', ' ', text)
    day_pat = re.compile(
        r"['\"]?(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)['\"]?\s*:\s*\[(.*?)\]",
        re.IGNORECASE | re.DOTALL,
    )
    field_pat = re.compile(r"([A-Za-z_]\w*)\s*[:=]\s*(?:'([^']*)'|\"([^\"]*)\")")

    result = {}
    for m in day_pat.finditer(compact):
        day = m.group(1).capitalize()
        classes = []
        for obj in re.finditer(r'\{(.*?)\}', m.group(2)):
            fields = {}
            for f in field_pat.finditer(obj.group(1)):
                value = f.group(2) if f.group(2) is not None else f.group(3)
                fields[f.group(1)] = value.strip() if isinstance(value, str) else value
            if not fields:
                continue
            if 'time' in fields:
                fields['time'] = time_to_24h(fields['time'])
            # GEMS uses 'RoomNo'; the rest of the app expects 'roomNo'
            if 'RoomNo' in fields and 'roomNo' not in fields:
                fields['roomNo'] = fields.pop('RoomNo')
            classes.append(fields)
        result[day] = classes

    return result or None

class GemsAPIClient:
    def __init__(self):
        # Use HTTP (not HTTPS) as discovered in HAR file
        self.base_url = "http://mitsims.in"
        self.session = requests.Session()
        self.last_error = None
        # Set user agent like the real GEMS app
        self.session.headers.update({
            'User-Agent': 'Dalvik/2.1.0 (Linux; U; Android 15; SM-G990B2 Build/AP3A.240905.015.A2)',
            'Connection': 'Keep-Alive',
            'Accept-Encoding': 'gzip',
            'Accept-Charset': 'UTF-8'  # Support Telugu characters
        })
    
    def login(self, username, password):
        """
        Login using the REAL GEMS API endpoint
        Discovered from HAR file: Uses GET method, not POST!
        """
        self.last_error = None
        print(f"🔐 Logging in as {username}...")
        
        # Exact endpoint from HAR file
        url = f"{self.base_url.rstrip('/')}/studentAppLogin/studentLogin.action"
        
        # Clean username and password (remove whitespace)
        username = str(username).strip()
        password = str(password).strip()
        
        # Exact parameters from HAR file
        params = {
            'actionType': 'studentAppLogin',
            'personType': 'student',
            'userId': username,
            'password': password
        }
        
        try:
            response = self.session.get(url, params=params, timeout=15)
            # Set UTF-8 encoding to handle Telugu characters
            response.encoding = 'utf-8'
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                # Response has single quotes and unquoted keys, need to handle
                text = response.text
                print(f"📄 Raw response: {text[:500]}")  # Debug: show raw response
                
                # Fix JavaScript-style JSON to proper JSON
                # 1. Add quotes around unquoted keys
                text = re.sub(r'(\w+)\s*:', r'"\1":', text)
                # 2. Fix single quotes to double quotes  
                text = text.replace("'", '"')
                
                # Try parsing - if control character error, sanitize and retry
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as e:
                    if 'control character' in str(e).lower():
                        print("⚠️ Control character error - applying sanitization for 3rd year student data")
                        text = sanitize_json_text(text)
                        data = json.loads(text)
                    else:
                        raise
                
                if data.get('status') == 'success':
                    details = data['studentLoginDetails'][0]
                    print(f"✅ Login successful!")
                    print(f"   Name: {details['name']}")
                    print(f"   Student ID: {details['id']}")
                    print(f"   Auth Token: {details['authToken'][:20]}...")
                    return details
                else:
                    error_msg = data.get('message', 'Login failed')
                    self.last_error = error_msg if error_msg else "Invalid username or password"
                    print(f"❌ Login failed: {data}")
                    print(f"💡 Error message: {error_msg}")
                    return None
            else:
                self.last_error = f"GEMS server returned HTTP {response.status_code}"
                print(f"❌ HTTP Error: {response.status_code}")
                print(f"Response: {response.text[:200]}")
                return None
                
        except requests.Timeout:
            self.last_error = "Connection to GEMS college portal timed out. The portal might be offline or slow."
            print(f"❌ Error: Request timed out")
            return None
        except Exception as e:
            self.last_error = f"Connection error: {e}"
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_attendance(self, student_details):
        """
        Get attendance using the REAL GEMS API endpoint
        Discovered from HAR file
        """
        print(f"\n📊 Fetching attendance...")
        
        student_id = student_details['id']
        auth_token = student_details['authToken']
        
        # Exact endpoint from HAR file
        url = f"{self.base_url.rstrip('/')}/studentApp/getAttendanceDetails.action"
        
        # Exact parameters from HAR file
        params = {
            'tkn': auth_token,
            'stdnt.id': student_id,
            'studentId': student_id,
            'actionType': 'attendanceDetails',
            'studentType': 'student'
        }
        
        try:
            response = self.session.get(url, params=params, timeout=15)
            # Set UTF-8 encoding to handle Telugu characters
            response.encoding = 'utf-8'
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                # Response uses JavaScript-style JSON (unquoted keys)
                # Need more aggressive parsing
                text = response.text
                
                # Save original for debugging
                print(f"Raw response (first 500 chars): {text[:500]}")
                
                # Use regex to fix JavaScript JSON to proper JSON
                # Add quotes around unquoted keys
                text = re.sub(r'(\w+):', r'"\1":', text)
                # Fix single quotes to double quotes  
                text = text.replace("'", '"')
                
                # Try parsing - if it fails due to control characters, sanitize and retry
                # (Issue affects some 3rd year students intermittently)
                try:
                    data = json.loads(text, strict=False)
                except json.JSONDecodeError as e:
                    if 'control character' in str(e).lower():
                        print("⚠️ Control character error - applying sanitization for 3rd year student data")
                        text = sanitize_json_text(text)
                        data = json.loads(text, strict=False)
                    else:
                        raise  # Re-raise if it's a different error
                
                if data.get('status') == 'success':
                    attendance_list = data['attendanceDetails']
                    print(f"✅ Found {len(attendance_list)} subjects!")
                    return attendance_list
                else:
                    print(f"❌ Failed to get attendance")
                    return None
            else:
                print(f"❌ HTTP Error: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_todays_classes(self, student_details):
        """
        Get today's classes using the REAL GEMS API endpoint
        This returns classes scheduled for today with time and status
        """
        print(f"\n📅 Fetching today's classes...")
        
        student_id = student_details['id']
        auth_token = student_details['authToken']
        
        # Get today's date in DD/MM/YY format (using Indian Standard Time - IST)
        now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
        
        # Try both 4-digit year (dd/MM/yyyy) and 2-digit year (dd/MM/yy)
        date_formats = [now_ist.strftime('%d/%m/%Y'), now_ist.strftime('%d/%m/%y')]
        
        url = f"{self.base_url.rstrip('/')}/studentApp/dashboard.action"
        
        for date_str in date_formats:
            params = {
                'tkn': auth_token,
                'stdnt.id': student_id,
                'studentId': student_id,
                'studentType': 'student',
                'dateString': date_str
            }
            
            try:
                response = self.session.get(url, params=params, timeout=15)
                response.encoding = 'utf-8'
                
                if response.status_code == 200:
                    text = response.text
                    print(f"📄 Today's classes raw ({date_str}): {text[:500]}")
                    
                    # Fix JavaScript-style JSON
                    text = re.sub(r'\s+', ' ', text)
                    text = text.replace("'", '"')
                    text = re.sub(r'"(\d{2}):(\d{2}):(\d{2})"', r'"\1|\2|\3"', text)
                    text = re.sub(r'(\w+)\s*:', r'"\1":', text)
                    text = text.replace('|', ':')
                    
                    try:
                        data = json.loads(text)
                    except Exception:
                        text = sanitize_json_text(text)
                        try:
                            data = json.loads(text)
                        except Exception as e:
                            print(f"⚠️ JSON parse error for {date_str}: {e}")
                            continue
                    
                    if isinstance(data, dict):
                        classes_raw = (
                            data.get('classDetails') or
                            data.get('todayClasses') or
                            data.get('classes') or
                            data.get('timeTableDetails') or
                            data.get('studentClassDetails') or
                            data.get('grid') or
                            data.get('data') or
                            []
                        )
                    elif isinstance(data, list):
                        classes_raw = data
                    else:
                        classes_raw = []
                    
                    if classes_raw:
                        print(f"✅ Found {len(classes_raw)} classes today with date {date_str}!")
                        # Normalize keys for consistency across frontend & backend
                        normalized_classes = []
                        for cls in classes_raw:
                            if not isinstance(cls, dict):
                                continue
                            print(f"  🔍 RAW class keys: {list(cls.keys())}")
                            print(f"  🔍 RAW class data: {cls}")
                            subj = cls.get('subjectName') or cls.get('subject') or cls.get('courseName') or cls.get('title') or ''
                            code = cls.get('code') or cls.get('subjectCode') or cls.get('courseCode') or ''
                            fac = cls.get('facultyName') or cls.get('faculty') or cls.get('employeeName') or cls.get('teacherName') or ''
                            time_val = cls.get('time') or cls.get('startTime') or cls.get('classTime') or '00:00:00'
                            room_val = cls.get('roomNo') or cls.get('room') or cls.get('roomName') or ''
                            
                            # ── GEMS status decoding ──
                            # GEMS returns attendance percentage in 'status' (e.g. "20.0", "0.0")
                            # or keywords like "Permission", "Scheduled", "Present", "Absent"
                            raw_st = str(cls.get('status') or cls.get('attStatus') or cls.get('attendanceStatus') or '').strip()
                            att_pct = str(cls.get('attPercentage') or '').strip()
                            
                            # Determine the real attendance status
                            raw_lower = raw_st.lower()
                            if raw_lower in ['present', 'p', 'p*', 'y', '1', 'attended']:
                                # Explicitly marked present
                                final_status = 'Present'
                            elif raw_lower == 'permission':
                                # Permission/leave — separate from present
                                final_status = 'Permission'
                            elif raw_lower in ['absent', 'a', 'a*', 'n', '0', 'bunked']:
                                # Explicitly marked absent
                                final_status = 'Absent'
                            elif raw_lower in ['scheduled', 'na', 'nc', '', 'none', 'null', 'success']:
                                # Not yet conducted
                                final_status = 'Not Updated'
                            else:
                                # GEMS often puts percentage in status field (e.g. "20.0", "0.0")
                                # If it's a number, the class WAS conducted but we can't tell P/A
                                try:
                                    float(raw_st)
                                    # It's a percentage - class was conducted
                                    # Check if attPercentage changed (> 0 means they attended at least once)
                                    final_status = 'Not Updated'
                                except ValueError:
                                    final_status = 'Not Updated'
                            
                            # Filter out placeholders / invalid subjects
                            subj_clean = str(subj).strip().upper()
                            if not subj_clean or subj_clean in ['FREE', 'NO CLASS', 'LUNCH', 'BREAK', 'LIBRARY', '-', 'UNKNOWN']:
                                continue
                            
                            normalized_classes.append({
                                'subjectName': subj,
                                'code': code,
                                'facultyName': fac,
                                'time': time_val,
                                'roomNo': room_val,
                                'status': final_status,
                                'attPercentage': cls.get('attPercentage', cls.get('percentage', None))
                            })
                        return normalized_classes
            except Exception as e:
                print(f"❌ Error fetching today's classes for {date_str}: {e}")
                continue
                
        print("❌ No classes found for today after checking all date formats.")
        return []
    
    def get_timetable(self, student_details):
        """
        Get timetable using the REAL GEMS API endpoint
        """
        print(f"\n🗓️  Fetching timetable...")
        
        student_id = student_details['id']
        auth_token = student_details['authToken']
        
        # Same endpoint, different action type
        url = f"{self.base_url.rstrip('/')}/studentApp/getAttendanceDetails.action"
        
        params = {
            'tkn': auth_token,
            'stdnt.id': student_id,
            'studentId': student_id,
            'actionType': 'timeTableDetails',
            'studentType': 'student'
        }
        
        try:
            response = self.session.get(url, params=params, timeout=15)
            response.encoding = 'utf-8'

            if response.status_code == 200:
                raw_text = response.text
                print(f"📄 Timetable raw (first 1500 chars): {raw_text[:1500]}")

                # GEMS uses mixed syntax: key='value' and key:'value'
                # Convert ALL to key:'value' format first
                text = re.sub(r"(\w+)='", r"\1:'", raw_text)  # key='value' → key:'value'
                text = re.sub(r'(\w+)="', r'\1:"', text)  # key="value" → key:"value"
                text = re.sub(r'(\w+)\s*:', r'\1:', text)  # remove spaces before :

                data = None
                try:
                    # Now try ast.literal_eval (Python dict format)
                    data = ast.literal_eval(text)
                    print(f"✅ Parsed with ast.literal_eval!")
                except Exception as e:
                    print(f"⚠️ ast failed: {e}")
                    # Next: convert to JSON
                    text = re.sub(r'(\w+):', r'"\1":', text)
                    text = text.replace("'", '"')

                    # Try parsing - if control character error, sanitize and retry
                    try:
                        try:
                            data = json.loads(text)
                        except json.JSONDecodeError as e2:
                            if 'control character' in str(e2).lower():
                                print("⚠️ Control character error - applying sanitization for 3rd year student data")
                                text = sanitize_json_text(text)
                                data = json.loads(text)
                            else:
                                raise
                    except Exception as e3:
                        print(f"⚠️ json failed: {e3}")
                        data = None

                timetable = None
                if isinstance(data, dict):
                    timetable = (
                        data.get('TimeTableDetails') or
                        data.get('timeTableDetails') or
                        data.get('timetableDetails') or
                        data.get('timeTable') or
                        data.get('grid') or
                        data.get('data')
                    )

                if not timetable:
                    # Last resort: regex-extract day blocks directly from the raw
                    # response — handles GEMS' malformed mixed JS syntax that
                    # neither ast.literal_eval nor json.loads can parse.
                    print("⚠️ Structured parse failed - using regex day-block extraction")
                    timetable = parse_timetable_js_text(raw_text)

                if timetable:
                    print(f"✅ Timetable retrieved!")
                    return timetable
                else:
                    print(f"❌ Failed to get timetable from response keys")
                    return None
            else:
                print(f"❌ HTTP Error: {response.status_code}")
                return None

        except Exception as e:
            print(f"❌ Error: {e}")
            return None

    DAY_NAMES = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')

    def _timetable_to_day_map(self, timetable_raw):
        """
        Normalize any raw GEMS timetable shape into {'Monday': [cls, ...], ...}.
        Handles:
        - dict keyed by day name: {'Monday': [...], ...}
        - list of single-key day dicts: [{'Monday': [...]}, {'Tuesday': [...]}]
        - list of tagged dicts: [{'day': 'Monday', 'classes': [...]}, ...]
        """
        day_map = {}
        if isinstance(timetable_raw, dict):
            for key, val in timetable_raw.items():
                key_clean = str(key).strip()
                if key_clean.lower() in self.DAY_NAMES and isinstance(val, list):
                    day_map[key_clean.capitalize()] = val
        elif isinstance(timetable_raw, list):
            for day_item in timetable_raw:
                if not isinstance(day_item, dict):
                    continue
                day = str(day_item.get('day', day_item.get('dayName', ''))).strip()
                if day:
                    classes_list = (
                        day_item.get('classDetails') or day_item.get('classes') or
                        day_item.get('periods') or []
                    )
                    day_map[day.capitalize()] = classes_list
                else:
                    # Single-key day dicts: {'Monday': [...]}
                    for key, val in day_item.items():
                        key_clean = str(key).strip()
                        if key_clean.lower() in self.DAY_NAMES and isinstance(val, list):
                            day_map[key_clean.capitalize()] = val
        return day_map

    def extract_today_from_timetable(self, timetable_raw):
        """
        Fallback: Extract today's classes from the full GEMS weekly timetable.
        """
        if not timetable_raw:
            return []

        now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
        today_name = now_ist.strftime('%A').capitalize()

        day_map = self._timetable_to_day_map(timetable_raw)
        today_classes = []
        for cls in day_map.get(today_name, []):
            if isinstance(cls, dict):
                today_classes.append({
                    'subjectName': cls.get('subjectName') or cls.get('subject') or cls.get('courseName') or 'Unknown',
                    'code': cls.get('code') or cls.get('subjectCode') or cls.get('courseCode') or '',
                    'facultyName': cls.get('facultyName') or cls.get('faculty') or cls.get('employeeName') or '',
                    'time': cls.get('time') or cls.get('startTime') or cls.get('classTime') or '00:00:00',
                    'roomNo': cls.get('roomNo') or cls.get('room') or '',
                    'status': cls.get('status') or 'NA',
                    'attPercentage': cls.get('attPercentage', None)
                })
        return today_classes

    def normalize_weekly_timetable(self, timetable_raw):
        """
        Convert raw GEMS weekly timetable into a clean format:
        { "Monday": [{subjectName, code, facultyName, time, roomNo}], ... }
        """
        if not timetable_raw:
            return None

        result = {}

        def _norm_cls(cls):
            subj = cls.get('subjectName') or cls.get('subject') or cls.get('courseName') or ''
            subj_clean = str(subj).strip().upper()
            if not subj_clean or subj_clean in ['FREE', 'NO CLASS', 'LUNCH', 'BREAK', 'LIBRARY', '-', 'UNKNOWN']:
                return None
            return {
                'subjectName': subj,
                'code': cls.get('code') or cls.get('subjectCode') or cls.get('courseCode') or '',
                'facultyName': cls.get('facultyName') or cls.get('faculty') or cls.get('employeeName') or '',
                'time': cls.get('time') or cls.get('startTime') or cls.get('classTime') or '00:00:00',
                'roomNo': cls.get('roomNo') or cls.get('room') or '',
            }

        for day, classes_list in self._timetable_to_day_map(timetable_raw).items():
            parsed_list = [_norm_cls(c) for c in classes_list if isinstance(c, dict)]
            result[day] = [c for c in parsed_list if c is not None]

        if result:
            total = sum(len(v) for v in result.values())
            print(f"📅 Weekly timetable normalized: {len(result)} days, {total} classes total")

        return result if result else None

    def scrape_full_data(self, username, password):
        """
        Complete scraping: Login + Attendance + Timetable
        """
        print("=" * 70)
        print("🚀 STARTING GEMS API SCRAPE (WORKING METHOD)")
        print("=" * 70)
        
        # Step 1: Login
        student_details = self.login(username, password)
        if not student_details:
            if not self.last_error:
                self.last_error = "Login failed. Check your username/USN and password."
            return None
        
        # Step 2: Get Attendance
        attendance = self.get_attendance(student_details)
        if not attendance:
            if not self.last_error:
                self.last_error = "Logged in successfully, but failed to fetch attendance records from GEMS."
            return None
        
        # Step 3: Get Today's Classes from dashboard.action
        todays_classes = self.get_todays_classes(student_details)

        # Step 4: Always fetch full weekly timetable (used by weekly planner & today resolution)
        tt_data = self.get_timetable(student_details)
        weekly_timetable = None

        # Extract today's classes from the official weekly timetable
        tt_today_classes = self.extract_today_from_timetable(tt_data) if tt_data else []

        if todays_classes:
            print(f"📅 Using live GEMS today's classes ({len(todays_classes)} classes with status)")
        elif tt_today_classes:
            print(f"📅 get_todays_classes empty. Fallback to weekly timetable for today ({len(tt_today_classes)} classes)")
            todays_classes = tt_today_classes

        # Normalize the weekly timetable into a frontend-friendly format
        if tt_data:
            weekly_timetable = self.normalize_weekly_timetable(tt_data)
        
        # Format result
        result = {
            'success': True,
            'student': {
                'id': student_details['id'],
                'name': student_details['name'],
                'usn': student_details['usn'],
                'email': student_details['email']
            },
            'attendance': [],
            'todaysClasses': todays_classes,
            'weeklyTimetable': weekly_timetable
        }
        
        # Parse attendance
        total_attended = 0
        total_conducted = 0
        total_attended_with_tt = 0
        total_conducted_with_tt = 0
        
        def safe_int(val, default=0):
            try:
                s = str(val).strip()
                return int(s) if s and s != '-' else default
            except Exception:
                return default

        def safe_float(val, default=0.0):
            try:
                s = str(val).strip()
                return float(s) if s and s != '-' else default
            except Exception:
                return default

        # Track today's marked Present classes by subject to fix GEMS midnight sync lag
        todays_present_counts = {}
        if todays_classes:
            for c in todays_classes:
                st = str(c.get('status', '')).lower()
                if st in ['present', 'p', 'p*', 'y', '1', 'attended']:
                    code_clean = str(c.get('code', '')).strip().upper()
                    name_clean = str(c.get('subjectName', '')).strip().upper()
                    if code_clean:
                        todays_present_counts[code_clean] = todays_present_counts.get(code_clean, 0) + 1
                    if name_clean and name_clean != code_clean:
                        todays_present_counts[name_clean] = todays_present_counts.get(name_clean, 0) + 1

        for subject in attendance:
            attended = safe_int(subject.get('attended', 0))
            conducted = safe_int(subject.get('conducted', 0))
            raw_percentage = safe_float(subject.get('percentage', 0))
            
            subj_code = str(subject.get('code', '')).strip().upper()
            subj_name = str(subject.get('subjectName', '')).strip().upper()
            
            # Count Present marks today for this subject
            present_today_count = max(
                todays_present_counts.get(subj_code, 0),
                todays_present_counts.get(subj_name, 0)
            )

            # Smart Real-Time Credit: If class marked Present today & GEMS hasn't credited +1 to attended yet
            adjusted_attended = attended
            if present_today_count > 0 and conducted > 0:
                raw_ratio_pct = round((attended / conducted) * 100, 2)
                # If GEMS raw percentage reflects un-credited (attended / conducted), add today's Present credit!
                if abs(raw_percentage - raw_ratio_pct) < 0.8:
                    adjusted_attended = min(conducted, attended + present_today_count)
                    print(f"✨ GEMS Real-Time Credit Applied for {subj_name}: Attended {attended} → {adjusted_attended}/{conducted} (+{present_today_count} Present today)")

            percentage = round((adjusted_attended / conducted * 100), 2) if conducted > 0 else raw_percentage

            result['attendance'].append({
                'subjectName': subject['subjectName'],
                'code': subject['code'],
                'attended': adjusted_attended,
                'rawAttended': attended,
                'conducted': conducted,
                'percentage': percentage,
                'gemsPendingSync': (adjusted_attended > attended)
            })
            
            # Calculate totals
            code = subject['code'].upper()
            if code not in ['TECHNICALTRAINING', 'APTITUDE', 'SOFTSKILLS']:
                # Count for official attendance (without technical training)
                total_attended += adjusted_attended
                total_conducted += conducted
            
            # Count for overall (with everything)
            total_attended_with_tt += adjusted_attended
            total_conducted_with_tt += conducted
        
        # Calculate percentages - NEW METHOD: Average of all subject percentages
        # Official: Average of non-TT subjects
        official_subjects = [s for s in result['attendance'] if s['code'].upper() not in ['TECHNICALTRAINING', 'APTITUDE', 'SOFTSKILLS']]
        overall_percentage = sum(s['percentage'] for s in official_subjects) / len(official_subjects) if official_subjects else 0
        
        # Overall: Average of ALL subjects including TT
        overall_with_tt = sum(s['percentage'] for s in result['attendance']) / len(result['attendance']) if result['attendance'] else 0
        
        # Add to result
        result['overallAttendance'] = {
            'percentage': round(overall_percentage, 2),
            'attended': total_attended,
            'conducted': total_conducted,
            'label': 'Official Attendance (excl. TT, Aptitude, Soft Skills)'
        }
        
        result['overallWithTT'] = {
            'percentage': round(overall_with_tt, 2),
            'attended': total_attended_with_tt,
            'conducted': total_conducted_with_tt,
            'label': 'Overall Attendance (with everything)'
        }
        
        print("\n" + "=" * 70)
        print("✅ SCRAPE COMPLETE!")
        print("=" * 70)
        print(f"Student: {result['student']['name']}")
        print(f"Subjects: {len(result['attendance'])}")
        print(f"\n📊 Official Attendance (excl. TT/Aptitude/Soft Skills): {overall_percentage:.2f}%")
        print(f"   Classes: {total_attended}/{total_conducted}")
        print(f"\n📊 Overall Attendance (with everything): {overall_with_tt:.2f}%")
        print(f"   Classes: {total_attended_with_tt}/{total_conducted_with_tt}")
        print("\n📊 Subject-wise Attendance:")
        
        for i, subject in enumerate(result['attendance'], 1):
            status = "🎉" if subject['percentage'] >= 75 else "⚠️" if subject['percentage'] >= 60 else "🚨"
            print(f"{i:2}. {subject['code']:20} {subject['attended']:2}/{subject['conducted']:2} = {subject['percentage']:5.2f}% {status}")
        
        return result

    @staticmethod
    def get_mock_data():
        """
        Realistic demo data for testing, offline preview, and showcase without needing live portal access.
        """
        return {
            'success': True,
            'isDemo': True,
            'student': {
                'id': '24892',
                'name': 'K. Sai Tharun',
                'usn': '21691A04G5',
                'email': '21691a04g5@mits.ac.in',
                'department': 'Electronics & Communication Engineering',
                'semester': 'VII Semester (B.Tech)',
                'section': 'ECE - C'
            },
            'attendance': [
                {
                    'subjectName': 'VLSI DESIGN',
                    'code': '20ECE114',
                    'attended': 33,
                    'rawAttended': 32,
                    'conducted': 38,
                    'percentage': 86.84,
                    'gemsPendingSync': True
                },
                {
                    'subjectName': 'DIGITAL SIGNAL PROCESSING',
                    'code': '20ECE115',
                    'attended': 28,
                    'rawAttended': 27,
                    'conducted': 36,
                    'percentage': 77.78,
                    'gemsPendingSync': True
                },
                {
                    'subjectName': 'EMBEDDED SYSTEMS & IOT',
                    'code': '20ECE116',
                    'attended': 34,
                    'rawAttended': 34,
                    'conducted': 40,
                    'percentage': 85.00,
                    'gemsPendingSync': False
                },
                {
                    'subjectName': 'VLSI & EMBEDDED SYSTEM LAB',
                    'code': '20ECE208',
                    'attended': 14,
                    'rawAttended': 14,
                    'conducted': 14,
                    'percentage': 100.00,
                    'gemsPendingSync': False
                },
                {
                    'subjectName': 'WIRELESS COMMUNICATION & NETWORKS',
                    'code': '20ECE117',
                    'attended': 22,
                    'rawAttended': 22,
                    'conducted': 34,
                    'percentage': 64.71,
                    'gemsPendingSync': False
                },
                {
                    'subjectName': 'MANAGERIAL ECONOMICS & FINANCIAL ANALYSIS',
                    'code': '20HUM102',
                    'attended': 24,
                    'rawAttended': 24,
                    'conducted': 32,
                    'percentage': 75.00,
                    'gemsPendingSync': False
                },
                {
                    'subjectName': 'TECHNICAL TRAINING (FULL STACK)',
                    'code': 'TECHNICALTRAINING',
                    'attended': 28,
                    'rawAttended': 28,
                    'conducted': 30,
                    'percentage': 93.33,
                    'gemsPendingSync': False
                },
                {
                    'subjectName': 'CAMPUS RECRUITMENT APTITUDE',
                    'code': 'APTITUDE',
                    'attended': 18,
                    'rawAttended': 18,
                    'conducted': 22,
                    'percentage': 81.82,
                    'gemsPendingSync': False
                },
                {
                    'subjectName': 'PROFESSIONAL COMMUNICATION & SOFT SKILLS',
                    'code': 'SOFTSKILLS',
                    'attended': 19,
                    'rawAttended': 19,
                    'conducted': 20,
                    'percentage': 95.00,
                    'gemsPendingSync': False
                }
            ],
            'todaysClasses': [
                {
                    'subjectName': 'VLSI DESIGN',
                    'code': '20ECE114',
                    'facultyName': 'Dr. K. Ramesh',
                    'time': '09:00:00',
                    'roomNo': 'EB-302',
                    'status': 'Present',
                    'attPercentage': '86.84'
                },
                {
                    'subjectName': 'DIGITAL SIGNAL PROCESSING',
                    'code': '20ECE115',
                    'facultyName': 'Prof. S. V. Naidu',
                    'time': '10:00:00',
                    'roomNo': 'EB-302',
                    'status': 'Present',
                    'attPercentage': '77.78'
                },
                {
                    'subjectName': 'WIRELESS COMMUNICATION & NETWORKS',
                    'code': '20ECE117',
                    'facultyName': 'Dr. M. Lakshmi',
                    'time': '11:15:00',
                    'roomNo': 'EB-304',
                    'status': 'Permission',
                    'attPercentage': '64.71'
                },
                {
                    'subjectName': 'VLSI & EMBEDDED SYSTEM LAB',
                    'code': '20ECE208',
                    'facultyName': 'Dr. K. Ramesh / P. Suresh',
                    'time': '13:30:00',
                    'roomNo': 'VLSI LAB-1',
                    'status': 'Not Updated',
                    'attPercentage': '100.00'
                },
                {
                    'subjectName': 'TECHNICAL TRAINING (FULL STACK)',
                    'code': 'TECHNICALTRAINING',
                    'facultyName': 'Mr. C. Venkat',
                    'time': '15:30:00',
                    'roomNo': 'CS LAB-4',
                    'status': 'Not Updated',
                    'attPercentage': '93.33'
                }
            ],
            'weeklyTimetable': {
                'Monday': [
                    {'subjectName': 'VLSI DESIGN', 'code': '20ECE114', 'facultyName': 'Dr. K. Ramesh', 'time': '09:00:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'DIGITAL SIGNAL PROCESSING', 'code': '20ECE115', 'facultyName': 'Prof. S. V. Naidu', 'time': '10:00:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'WIRELESS COMMUNICATION & NETWORKS', 'code': '20ECE117', 'facultyName': 'Dr. M. Lakshmi', 'time': '11:15:00', 'roomNo': 'EB-304'},
                    {'subjectName': 'VLSI & EMBEDDED SYSTEM LAB', 'code': '20ECE208', 'facultyName': 'Dr. K. Ramesh', 'time': '13:30:00', 'roomNo': 'VLSI LAB-1'},
                    {'subjectName': 'TECHNICAL TRAINING (FULL STACK)', 'code': 'TECHNICALTRAINING', 'facultyName': 'Mr. C. Venkat', 'time': '15:30:00', 'roomNo': 'CS LAB-4'}
                ],
                'Tuesday': [
                    {'subjectName': 'EMBEDDED SYSTEMS & IOT', 'code': '20ECE116', 'facultyName': 'Dr. V. Prasad', 'time': '09:00:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'VLSI DESIGN', 'code': '20ECE114', 'facultyName': 'Dr. K. Ramesh', 'time': '10:00:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'MANAGERIAL ECONOMICS & FINANCIAL ANALYSIS', 'code': '20HUM102', 'facultyName': 'Dr. R. Anand', 'time': '11:15:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'DIGITAL SIGNAL PROCESSING', 'code': '20ECE115', 'facultyName': 'Prof. S. V. Naidu', 'time': '13:30:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'CAMPUS RECRUITMENT APTITUDE', 'code': 'APTITUDE', 'facultyName': 'Aptitude Cell', 'time': '14:30:00', 'roomNo': 'EB-302'}
                ],
                'Wednesday': [
                    {'subjectName': 'WIRELESS COMMUNICATION & NETWORKS', 'code': '20ECE117', 'facultyName': 'Dr. M. Lakshmi', 'time': '09:00:00', 'roomNo': 'EB-304'},
                    {'subjectName': 'EMBEDDED SYSTEMS & IOT', 'code': '20ECE116', 'facultyName': 'Dr. V. Prasad', 'time': '10:00:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'VLSI DESIGN', 'code': '20ECE114', 'facultyName': 'Dr. K. Ramesh', 'time': '11:15:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'MANAGERIAL ECONOMICS & FINANCIAL ANALYSIS', 'code': '20HUM102', 'facultyName': 'Dr. R. Anand', 'time': '13:30:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'PROFESSIONAL COMMUNICATION & SOFT SKILLS', 'code': 'SOFTSKILLS', 'facultyName': 'Trainer', 'time': '14:30:00', 'roomNo': 'LANG LAB'}
                ],
                'Thursday': [
                    {'subjectName': 'DIGITAL SIGNAL PROCESSING', 'code': '20ECE115', 'facultyName': 'Prof. S. V. Naidu', 'time': '09:00:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'WIRELESS COMMUNICATION & NETWORKS', 'code': '20ECE117', 'facultyName': 'Dr. M. Lakshmi', 'time': '10:00:00', 'roomNo': 'EB-304'},
                    {'subjectName': 'EMBEDDED SYSTEMS & IOT', 'code': '20ECE116', 'facultyName': 'Dr. V. Prasad', 'time': '11:15:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'TECHNICAL TRAINING (FULL STACK)', 'code': 'TECHNICALTRAINING', 'facultyName': 'Mr. C. Venkat', 'time': '13:30:00', 'roomNo': 'CS LAB-4'}
                ],
                'Friday': [
                    {'subjectName': 'MANAGERIAL ECONOMICS & FINANCIAL ANALYSIS', 'code': '20HUM102', 'facultyName': 'Dr. R. Anand', 'time': '09:00:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'VLSI DESIGN', 'code': '20ECE114', 'facultyName': 'Dr. K. Ramesh', 'time': '10:00:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'DIGITAL SIGNAL PROCESSING', 'code': '20ECE115', 'facultyName': 'Prof. S. V. Naidu', 'time': '11:15:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'EMBEDDED SYSTEMS & IOT', 'code': '20ECE116', 'facultyName': 'Dr. V. Prasad', 'time': '13:30:00', 'roomNo': 'EB-302'}
                ],
                'Saturday': [
                    {'subjectName': 'CAMPUS RECRUITMENT APTITUDE', 'code': 'APTITUDE', 'facultyName': 'Aptitude Cell', 'time': '09:00:00', 'roomNo': 'EB-302'},
                    {'subjectName': 'TECHNICAL TRAINING (FULL STACK)', 'code': 'TECHNICALTRAINING', 'facultyName': 'Mr. C. Venkat', 'time': '10:30:00', 'roomNo': 'CS LAB-4'},
                    {'subjectName': 'PROFESSIONAL COMMUNICATION & SOFT SKILLS', 'code': 'SOFTSKILLS', 'facultyName': 'Trainer', 'time': '13:30:00', 'roomNo': 'LANG LAB'}
                ]
            },
            'overallAttendance': {
                'percentage': 81.55,
                'attended': 157,
                'conducted': 194,
                'label': 'Official Attendance (excl. TT, Aptitude, Soft Skills)'
            },
            'overallWithTT': {
                'percentage': 84.89,
                'attended': 222,
                'conducted': 266,
                'label': 'Overall Attendance (with everything)'
            }
        }

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python gems_api_working.py <username> <password>")
        sys.exit(1)
    
    username = sys.argv[1]
    password = sys.argv[2]
    
    client = GemsAPIClient()
    result = client.scrape_full_data(username, password)
    
    if result:
        print("\n" + "=" * 70)
        print("📄 JSON OUTPUT:")
        print("=" * 70)
        print(json.dumps(result, indent=2))
    else:
        print("\n❌ Scraping failed!")
        sys.exit(1)
