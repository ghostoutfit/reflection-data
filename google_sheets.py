import gspread
from oauth2client.service_account import ServiceAccountCredentials
import streamlit as st

# --- Connect to Google Sheets ---
def connect_to_sheets():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        st.secrets["google_service_account"], scope)
    client = gspread.authorize(creds)
    return client

def get_sheet(sheet_name):
    client = connect_to_sheets()

    # --- RECOMMENDED: Open by spreadsheet ID for stability ---
    spreadsheet = client.open_by_key("1UCV4mKpdJPUy8ywZlkicI-5YZAoRWV6REsF3dz7EgAI")
    return spreadsheet.worksheet(sheet_name)

    # --- ALTERNATIVE (not recommended): Open by spreadsheet name ---
    # This method uses the Google Drive API to search by title.
    # It is more fragile: requires perfect name match, relies on Drive API permissions,
    # and may silently fail if multiple sheets have the same title or the name changes.
    #
    # spreadsheet = client.open("GoalReflectionApp_StudentData")
    # return spreadsheet.worksheet(sheet_name)


# --- Add new student if they don't exist ---
def create_student_if_missing(student_id, nickname="", pronoun_code="", tone="Reflective"):
    sheet = get_sheet("Students")
    existing = get_student_info(student_id)
    if existing:
        return False  # already exists

    headers = sheet.row_values(1)
    row_data = {
        "StudentID": student_id,
        "Nickname": nickname,
        "PronounCode": pronoun_code,
        "ChosenTone": tone,
        "CurrentGoal": "",
        "CurrentSuccessMeasures": "",
        "CurrentGoalSetDate": "",
        "GoalRange": "",  # will be inferred later
        "BackgroundInfo": ""  # will be inferred later
    }
    row = [row_data.get(col, "") for col in headers]
    sheet.append_row(row)
    return True



# --- Fetch student info from "Students" sheet by StudentID ---
def get_student_info(student_id):
    sheet = get_sheet("Students")
    records = sheet.get_all_records()
    for row in records:
        if str(row["StudentID"]).strip() == str(student_id).strip():
            return row
    return None

# --- Append a new row to GoalHistory ---
def add_goal_history_entry(entry_dict):
    sheet = get_sheet("GoalHistory")
    headers = sheet.row_values(1)
    row = [entry_dict.get(header, "") for header in headers]
    sheet.append_row(row)

# --- Update the student’s current goal and related info ---
def update_student_current_goal(student_id, new_goal, new_success_measures, set_date, goal_range=None, background_info=None):
    sheet = get_sheet("Students")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if str(row["StudentID"]).strip() == str(student_id).strip():
            row_num = i + 2  # account for header row
            sheet.update_cell(row_num, 5, new_goal)               # CurrentGoal
            sheet.update_cell(row_num, 6, new_success_measures)   # CurrentSuccessMeasures
            sheet.update_cell(row_num, 7, set_date)               # CurrentGoalSetDate
            if goal_range is not None:
                sheet.update_cell(row_num, 8, goal_range)         # GoalRange
            if background_info is not None:
                sheet.update_cell(row_num, 9, background_info)    # BackgroundInfo
            return True
    return False

