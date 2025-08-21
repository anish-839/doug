#import os
import requests
from dotenv import load_dotenv
from twilio.rest import Client
import json
import time
import pdfplumber  # Add this library to extract text from PDFs
from bs4 import BeautifulSoup
from openai import OpenAI
import json
import sqlite3
#import re
from datetime import datetime, timedelta
from typing import Optional




import os, re, base64, tempfile
from typing import Tuple, Optional, Dict, Any
from datetime import datetime
#from bs4 import BeautifulSoup

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

load_dotenv()

ACCOUNT_SID = os.getenv("TWILIO_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

# Twilio Sandbox WhatsApp number (DO NOT change this)
FROM_NUMBER = 'whatsapp:+14155238886'  # Always this for sandbox
TWILIO_NUMBER='+17755102353'
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Make sure the variable name matches your .env
client = OpenAI(api_key=OPENAI_API_KEY)

twilio = Client(ACCOUNT_SID, AUTH_TOKEN)



API_KEY = os.getenv("LOXO_API")
AGENCY_SLUG = os.getenv("LOXO_AGENCY_SLUG")
BASE = f"https://app.loxo.co/api/{AGENCY_SLUG}"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

#EXPECTED_EMAIL = "runningoutofuniqueemail@gmail.com"  # Hardcoded for now

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# ---------- OAuth / Service ----------

def get_gmail_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)

# ---------- Helpers ----------

def _b64url_to_bytes(s: str) -> bytes:
    if not s:
        return b""
    padding = 4 - (len(s) % 4)
    if padding and padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)

def _walk_parts(payload: Dict[str, Any]):
    stack = [payload]
    while stack:
        part = stack.pop()
        yield part
        for p in part.get("parts", []) or []:
            stack.append(p)

def _get_subject(msg: Dict[str, Any]) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name") == "Subject":
            return h.get("value", "")
    return ""

def _get_html_and_text(payload) -> Tuple[Optional[str], Optional[str]]:
    html, text = None, None
    for part in _walk_parts(payload):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if not data:
            continue
        try:
            raw = _b64url_to_bytes(data).decode("utf-8", errors="ignore")
        except Exception:
            continue
        if mime == "text/html" and html is None:
            html = raw
        elif mime == "text/plain" and text is None:
            text = raw
    return html, text

def _parse_name_and_title(html: Optional[str], text: Optional[str], subject: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    def parse_lines(lines):
        cand, title = None, None
        for i, line in enumerate(lines):
            if line.lower().endswith("applied"):
                # strip trailing separators before "applied"
                cand = line[: -len("applied")].strip(" -•,").strip()
                if i + 1 < len(lines):
                    nxt = lines[i + 1]
                    # split on common separators after the name line
                    title = re.split(r"[•,|\-–—]", nxt)[0].strip()
                break
        return cand, title

    # Extract state code from subject: take the final ", XX"
    state_code = None
    if subject:
        m_state = re.search(r",\s*([A-Za-z]{2})\s*$", subject.strip())
        if m_state:
            state_code = m_state.group(1).upper()

    # Try HTML first
    if html:
        soup = BeautifulSoup(html, "html.parser")
        lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
        cand, title = parse_lines(lines)
        if cand or title:
            return cand, title, state_code

    # Fallback to plain text
    if text:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        cand, title = parse_lines(lines)
        if cand or title:
            return cand, title, state_code

    # Final fallback: derive job title from subject
    jt = None
    if subject:
        m = re.search(r"New application for\s*(.*?)(?:,|$)", subject, re.I)
        if m:
            jt = m.group(1).strip()

    return None, jt, state_code

def _safe_filename(name: str) -> str:
    # keep alnum, dot, dash, underscore, space
    safe = re.sub(r"[^A-Za-z0-9.\- _]", "_", name).strip()
    return safe or "attachment"

def _unique_path(directory: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base} ({i}){ext}")
        i += 1
    return candidate

def _download_first_resume_attachment(service, msg, download_dir: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Downloads the first PDF/DOC/DOCX attachment.
    Returns (file_path, filename) or (None, None).
    """
    for part in _walk_parts(msg.get("payload", {})):
        filename = part.get("filename", "")
        if not filename:
            continue
        if not filename.lower().endswith((".pdf", ".doc", ".docx")):
            continue

        body = part.get("body", {})
        att_id = body.get("attachmentId")
        if not att_id:
            continue

        att = service.users().messages().attachments().get(
            userId="me", messageId=msg["id"], id=att_id
        ).execute()
        file_bytes = _b64url_to_bytes(att.get("data", ""))

        # Decide where to save
        if download_dir:
            os.makedirs(download_dir, exist_ok=True)
            safe_name = _safe_filename(filename)
            path = _unique_path(download_dir, safe_name)
            with open(path, "wb") as f:
                f.write(file_bytes)
        else:
            # temp file fallback
            fd, path = tempfile.mkstemp(prefix="resume_", suffix=os.path.splitext(filename)[1] or ".bin")
            os.close(fd)
            with open(path, "wb") as f:
                f.write(file_bytes)

        return path, filename
    return None, None



def save_processed_emails(processed_set):
    """Save processed email IDs to JSON file"""
    try:
        with open(PROCESSED_EMAILS_FILE, 'w') as f:
            json.dump(list(processed_set), f)
    except Exception as e:
        print(f"Error saving processed emails: {e}")

def mark_email_as_processed_in_gmail(service, message_id):
    """Add 'processed' label to email in Gmail"""
    try:
        # First, get or create the 'processed' label
        labels = service.users().labels().list(userId='me').execute()
        processed_label_id = None
        
        for label in labels.get('labels', []):
            if label['name'].lower() == 'processed':
                processed_label_id = label['id']
                break
        
        # Create label if it doesn't exist
        if not processed_label_id:
            label_object = {
                'name': 'processed',
                'messageListVisibility': 'show',
                'labelListVisibility': 'labelShow'
            }
            created_label = service.users().labels().create(userId='me', body=label_object).execute()
            processed_label_id = created_label['id']
        
        # Add the label to the message
        modify_request = {
            'addLabelIds': [processed_label_id]
        }
        
        service.users().messages().modify(
            userId='me', 
            id=message_id, 
            body=modify_request
        ).execute()
        
        print(f"✅ Marked email {message_id} as processed in Gmail")
        
    except Exception as e:
        print(f"❌ Error marking email as processed in Gmail: {e}")

# ---------- Public function ----------

def send_sms_message(to_number):
    """
    Sends an SMS using the Twilio API.

    :param to_number: The recipient's verified phone number (in the format: '+91-9833944247')
    :return: SID of the sent message
    """
    
    # Remove hyphens and format the number correctly
    formatted_number = to_number.replace('-', '').replace(' ', '')

    message = twilio.messages.create(
        body="Your application has been submitted successfully! While we process your resume, we'd love to ask you a few quick follow-up questions. Is that okay?",
        from_=TWILIO_NUMBER,
        to=formatted_number
    )
    print(f"✅ SMS message sent. SID: {message.sid}")
    return message.sid



def extract_text_from_pdf(pdf_file):
    try:
        with pdfplumber.open(pdf_file) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text()
            return text
    except Exception as e:
        print(f"❌ Failed to extract text from PDF. Error: {e}")
        return None

def fetch_application(query: str, download_dir: Optional[str] = None, max_results: int = 60):
    """
    Simplified version - just fetch emails, Gmail labels handle the filtering
    """
    service = get_gmail_service()
    
    # Fetch emails (Gmail query already excludes processed ones with -label:processed)
    resp = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    msgs = resp.get("messages", [])
    
    print(f"📧 Gmail returned {len(msgs)} emails")

    results = []
    
    for msg in msgs:
        message_id = msg["id"]
            
        try:
            msg_detail = service.users().messages().get(userId="me", id=message_id, format="full").execute()
            subject = _get_subject(msg_detail)
            html, text = _get_html_and_text(msg_detail.get("payload", {}))
            candidate_name, job_title, state_code = _parse_name_and_title(html, text, subject)
            resume_path, resume_filename = _download_first_resume_attachment(service, msg_detail, download_dir)

            result = {
                'candidate_name': candidate_name,
                'job_title': job_title,
                'resume_path': resume_path,
                'resume_filename': resume_filename,
                'message_id': message_id,
                'subject': subject,
                'state_code': state_code,
                'fetched_at': datetime.now().isoformat()
            }
            results.append(result)
            
        except Exception as e:
            print(f"❌ Error fetching email {message_id}: {e}")

    print(f"📊 New emails to process: {len(results)}")
    return results


# Function to search for a person by name or email
def search_person_by_name(name_or_email):
    url = f"{BASE}/people?query={name_or_email}&per_page=5"
    
    print(f"🔍 Hitting: {url}")
    resp = requests.get(url, headers=HEADERS)
    print(f"🔢 Status Code: {resp.status_code}")
    resp.raise_for_status()
    
    data = resp.json()
    total_count = data.get("total_count", 0)
    print(f"📊 Total count of people: {total_count}")
    
    people = data.get("people", [])
    
    if not people:
        print("⚠️ No people found.")
        return None
    
    for person in people:
        print(f"🔍 Checking person: {person['name']}")
        print(f"Person object: {person}")
        
        # Get the phone number (assuming it's under 'phone_numbers' field)
        # phone_number = None
        # if 'phone_numbers' in person:
        #     phone_number = person.get('phone_numbers', [])[0].get('value', None)
        
        for email in person.get('emails', []):
            person_email = email.get('value')
            if person_email == EXPECTED_EMAIL:
                print(f"✅ Found matching candidate: {person['name']}")
                person_id = person.get('id')
                phone_numbers = person.get('phones', [])
                phone_number = phone_numbers[0].get('value') if phone_numbers else None
                print(f"📱 Phone number: {phone_number}")
                person_name = person.get('name')
                
                
                return person_name, person_id, phone_number
    
    return None

def insert_candidate_for_automation(person_id, job_id, person_phone, candidate_name, resume_score):
    # Connect to SQLite database
    normalized_phone = normalize_phone_number(person_phone)
    conn = sqlite3.connect('app.db')
    cursor = conn.cursor()

    # Insert candidate data into candidate_job_mapping table
    cursor.execute('''
    INSERT INTO candidate_job_mapping (person_id, job_id, person_phone, candidate_name, resume_score)
    VALUES (?, ?, ?, ?, ?)
    ''', (person_id, job_id, normalized_phone, candidate_name, resume_score))

    # Commit the changes and close the connection
    conn.commit()
    conn.close()

    return None

# Function to find job by title
def find_job_by_title(title, state_code):
    url = f"{BASE}/jobs?query={title}&per_page=5&page=1"
    print(f"🔍 Hitting: {url}")
    resp = requests.get(url, headers=HEADERS)
    print(f"🔢 Status Code: {resp.status_code}")
    resp.raise_for_status()
    
    data = resp.json()
    total_count = data.get("total_count", 0)
    print(f"📊 Total count of jobs: {total_count}")
    
    jobs = data.get("results", [])
    
    if not jobs:
        print("⚠️ No jobs found.")
        return None, None
    
    for job in jobs:
        print(f"🔍 Checking job: {job['title']}")
        
        if title.lower() in job.get("title", "").lower() and state_code.upper() in job.get("state_code", "").upper():
            print(f"✅ Found matching job: {job['title']}")
            job_id = job.get('id')
            return job, job_id
    
    return None, None

# Function to download resume


def save_job_description(job_title, job_description):
    """Inserts a new job description into the database."""
    conn = sqlite3.connect('job_descriptions.db')  # Connect to the database
    cursor = conn.cursor()
    
    # Insert job title and job description into the table
    cursor.execute("INSERT INTO job_descriptions (job_title, job_description) VALUES (?, ?)",
                   (job_title, job_description))
    
    conn.commit()  # Commit the transaction
    conn.close()

# Function to extract text from resume PDF
def extract_text_from_pdf(pdf_file):
    try:
        with pdfplumber.open(pdf_file) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text()
            return text
    except Exception as e:
        print(f"❌ Failed to extract text from PDF. Error: {e}")
        return None

# Function to retrieve the job description
def retrieve_job_description(job_id):
    url = f"{BASE}/jobs/{job_id}"
    print(f"🔍 Hitting: {url}")
    resp = requests.get(url, headers=HEADERS)
    print(f"🔢 Status Code: {resp.status_code}")
    resp.raise_for_status()
    
    job_data = resp.json()
    job_description = job_data.get('description', 'No description available.')
    
    # Remove HTML tags using BeautifulSoup
    clean_description = BeautifulSoup(job_description, "html.parser").get_text()
    
    print(f"📄 Cleaned Job Description: {clean_description}")
    return clean_description






# Function to evaluate candidate with LLM
def evaluate_candidate_with_llm(resume_text, clean_description):
    """Use OpenAI to evaluate candidate fit for the job"""

    prompt = f"""
You are an expert recruiter. Evaluate this candidate for the given job position.

**Job Details:**
Job Description: {clean_description}
Primary Skills: Python, LangChain, LLM APIs, FAISS, Weaviate, Pinecone, and more

**Candidate Profile:**
Resume Content: {resume_text}

**Please provide evaluation in this JSON format:**
{{
    "overall_score": <number between 0-100>,
    "recommendation": "<HIRE/INTERVIEW/PASS>",
    "strengths": ["strength1", "strength2", "strength3"],
    "concerns": ["concern1", "concern2"],
    "skills_match": <number between 0-100>,
    "experience_match": <number between 0-100>,
    "summary": "<brief 2-3 sentence evaluation summary>"
}}

Focus on:
1. Skills alignment with job requirements
2. Experience level match
3. Career progression relevance
4. Overall fit for the role
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an expert technical recruiter with 10+ years of experience in candidate evaluation."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=1500
        )

        # Extract message content properly using dot notation
        message_content = response.choices[0].message.content.strip()
        print(f"📊 Raw LLM Response:\n{message_content}")

        # Remove markdown-style ```json and ``` if present
        if message_content.startswith("```json"):
            message_content = message_content.replace("```json", "").replace("```", "").strip()
        elif message_content.startswith("```"):
            message_content = message_content.replace("```", "").strip()

        # Try to parse the cleaned JSON
        try:
            evaluation = json.loads(message_content)
        except json.JSONDecodeError:
            evaluation = {
                "overall_score": 50,
                "recommendation": "REVIEW_NEEDED",
                "strengths": ["Unable to parse evaluation"],
                "concerns": ["LLM response parsing failed"],
                "skills_match": 50,
                "experience_match": 50,
                "summary": "Evaluation failed - manual review required"
            }

        return evaluation

    except Exception as e:
        print(f"❌ Error in LLM evaluation: {str(e)}")
        return {
            "overall_score": 0,
            "recommendation": "ERROR",
            "strengths": [],
            "concerns": [f"API Error: {str(e)}"],
            "skills_match": 0,
            "experience_match": 0,
            "summary": "Error occurred during evaluation"
        }

#import re

def normalize_phone_number(phone_number):
    """Normalize the phone number by stripping out non-numeric characters."""
    return ''.join(re.findall(r'\d', phone_number))  # Extracts only digits


def send_whatsapp_message(to_number):
    """
    Sends a WhatsApp message using the Twilio API.

    :param to_number: The recipient's verified WhatsApp number (in the format: '+91-9833944247')
    :return: SID of the sent message
    """
    
    # Remove hyphens and format the number correctly
    formatted_number = 'whatsapp:' + to_number.replace('-', '').replace(' ', '')

    message = twilio.messages.create(
        body="Your application has been submitted successfully! While we process your resume, we'd love to ask you a few quick follow-up questions. Is that okay?",
        from_=FROM_NUMBER,
        to=formatted_number
    )
    print(f"✅ WhatsApp message sent. SID: {message.sid}")
    return message.sid


def extract_email(resume_text):
    # Define a regular expression for matching an email address
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    
    # Use re.findall to find all matches of the email pattern in the resume text
    emails = re.findall(email_pattern, resume_text)
    
    # If there is at least one email address found, return the first one
    if emails:
        return emails[0]
    else:
        return None

# Main function to process candidate resume
def process_candidate_resume(job_id):
    # Download the resume
    
    
    # Extract text from the downloaded resume
    
    
    # Retrieve the job description
    job_description = retrieve_job_description(job_id)

    # Send the resume text and job description to LLM for evaluation
    evaluation_result = evaluate_candidate_with_llm(resume_text, job_description)
    #evaluation_result = "good"
    print(f"📝 Evaluation Result: {evaluation_result}")

    return job_description , evaluation_result



# Example usage
if __name__ == "__main__":
    print("Choose automation mode:")
    print("1. Run once (process current emails)")
    print("2. Run continuously (every 10 minutes)")
    
    choice = input("Enter choice (1 or 2): ").strip()
    
    if choice == "2":
        print("🚀 Starting continuous email automation...")
        print("⏰ Will check for new emails every 10 minutes")
        print("🛑 Press Ctrl+C to stop")
    
    cycle_count = 0

    # if choice == 1, we start are automation of th fetched mail and break the while true loop at then end 
    # if choice == 2 , we stay in infinite while loop which waits 10 mins for next cycle to fetch new mails
    
    try:
        while True:
            cycle_count += 1
            print(f"\n{'='*60}")
            print(f"🔄 Starting cycle #{cycle_count} at {datetime.now()}")
            print(f"{'='*60}")
            
            # Updated query to exclude processed emails and fetch more
            query = 'subject:"[Action required] New application for" has:attachment -label:processed newer_than:20d'
            #download_folder = r"C:\Users\LENOVO\Desktop\work_please\resume"
            download_folder = os.path.join(os.path.dirname(__file__), 'resume')

            # Fetch 50-60 applications
            results = fetch_application(query, download_dir=download_folder)
            successful = 0
            failed = 0
            if not results:
                print("📭 No new applications found.")
            else:
                print(f"📧 Found {len(results)} new applications to process")
                
                
                
                with open("automation_logs.txt", "a", encoding="utf-8") as f:
                    f.write(f"\n{'='*50}\n")
                    f.write(f"🕐 Cycle #{cycle_count} started at: {datetime.now()}\n")
                    f.write(f"📧 Processing {len(results)} applications\n")
                    f.write(f"{'='*50}\n")
                    
                    # YOUR EXISTING LOGIC STARTS HERE - exactly as you had it
                    for i, result in enumerate(results, 1):
                        try:
                            print(f"\n📊 Processing {i}/{len(results)}: {result['candidate_name']}")
                            
                            stream_name = result['candidate_name']
                            message_id = result['message_id']
                            f.write(f"📧 Processing {i}/{len(results)}: {stream_name}\n")
                            
                            """we fetch candidate name, job title , job state code, and download resume from mail """

                            resume_path = result['resume_path']
                            if not resume_path or not os.path.exists(resume_path):
                                raise FileNotFoundError("Resume file not found")

                            resume_text = extract_text_from_pdf(resume_path)
                            print(resume_text)
                            
                            """expected mail is extracted from resume text which is matched on loxo to identify the correct candidate """

                            EXPECTED_EMAIL = extract_email(resume_text)
                            print(EXPECTED_EMAIL)
                            f.write("✓ Fetched Resume Text and Found Email\n")
                            
                            candidate_name_or_email = result['candidate_name'] 
                            job_title = result['job_title']
                            state_code = result['state_code']

                            """finds the candidate on loxo by providing name and email"""

                            print(f"🔍 Looking for candidate: {candidate_name_or_email}")
                            person, person_id, phone_number = search_person_by_name(candidate_name_or_email)
                            if not person:
                                print("❌ Candidate not found.")
                                f.write("❌ Candidate not found, skipping...\n")
                                failed += 1
                                continue  # Changed from exit() to continue
                                
                            f.write("✓ Found Person\n")
                            send_sms_message(phone_number)
                            print(f"📱 Sending automated message to: {phone_number}")

                            f.write("✓ Sent Automated Text\n")

                            """Finds person document from the perosn id fetched above"""

                            url = f"https://app.loxo.co/api/{AGENCY_SLUG}/people/{person_id}"

                            headers = {
                                "accept": "application/json",
                                "authorization": f"Bearer {API_KEY}"
                            }

                            response = requests.get(url, headers=headers)

                            if response.status_code == 200:
                                person_data = response.json()
                                person_desc = person_data.get('description', '')
                                if person_desc:
                                    person_desc = BeautifulSoup(person_desc, 'html.parser').get_text()
                            else:
                                person_desc = ""

                            print(person_desc)

                            """Finds job id by searching job title and matching with state code extracted from mail"""

                            print(f"🔍 Looking for job: {job_title}")
                            job, job_id = find_job_by_title(job_title, state_code)
                            if not job:
                                print("❌ Job not found.")
                                f.write("❌ Job not found, skipping...\n")
                                failed += 1
                                continue  # Changed from exit() to continue
                                
                            f.write("✓ Found Matching Job\n")
                            
                            print("🔗 Processing candidate's resume and job description...")
                            job_description, evaluation_result = process_candidate_resume(job_id)
                            
                            print(job_id)
                            print(person)
                            print(phone_number)
                            f.write("✓ Evaluated Candidate\n")
                            
                            overall_score = evaluation_result['overall_score']
                            
                            """passes job description extracted from job id and resume text to llm for evualation which returns a json format result"""

                            #insert_candidate_for_automation(person_id, job_id, phone_number, person, overall_score)
                            """ adds the person to job """

                            url = f"https://app.loxo.co/api/{AGENCY_SLUG}/jobs/{job_id}/apply"
                            with open(resume_path, "rb") as resume_file:
                                files = { "resume": (resume_path, resume_file, "application/pdf") }
                                payload = {
                                    "name": f"{person}",
                                    "phone": f"{phone_number}",
                                    "email": f"{EXPECTED_EMAIL}",
                                    "source_type_id": "2028652",
                                }
                                headers = {
                                    "accept": "application/json",
                                    "authorization": f"Bearer {API_KEY}"
                                }

                                response = requests.post(url, data=payload, files=files, headers=headers)

                            f.write("✓ Candidate Added to Job\n")

                            #response = requests.post(url, data=payload, files=files, headers=headers)
                            f.write("✓ Candidate Added to Job\n")
                            if os.path.exists(resume_path):
                                os.remove(resume_path)
                                print(f"✅ Deleted the resume file: {resume_path}")
                            else:
                                print(f"❌ Resume file not found: {resume_path}")
                            time.sleep(5)
                            summary = evaluation_result['summary']
                            email_id = extract_email(resume_text)
                            print(f"Extracted Email ID: {email_id}")

                            if person_desc is None:
                                person_desc = ""  # Ensure it's initialized as an empty string

                            person_desc += f"\n\nSummary: {summary}\n\nOverall Score: {overall_score}" 

                            """person description is appended along with llm summary an overall score and passed as string to our api call"""

                            print(person_desc)
                            f.write(f"Candidate Score: {overall_score}\n")
                            
                            if overall_score > 60:
                                ah_tag = "AI Accepted"
                                activity_type_id = 760300
                            else:
                                ah_tag = "AI Rejected"
                                activity_type_id = 760312

                            source_type_id = 429885
                            time.sleep(5)

                            """the person added to job (which is in applied stage rn), we add a tag, soruce id, and person description"""

                            url = f"https://app.loxo.co/api/{AGENCY_SLUG}/people/{person_id}"
                            payload = f"""-----011000010111000001101001\r\nContent-Disposition: form-data; name="source_type_id"\r\n\r\n{source_type_id}\r\n-----011000010111000001101001\r\nContent-Disposition: form-data; name="job_id"\r\n\r\n{job_id}\r\n-----011000010111000001101001\r\nContent-Disposition: form-data; name="person[raw_tags][]"\r\n\r\n{ah_tag}\r\n-----011000010111000001101001\r\nContent-Disposition: form-data; name="person[description]"\r\n\r\n{person_desc}\r\n-----011000010111000001101001\r\nContent-Disposition: form-data; name="person[source_type_id]"\r\n\r\n{source_type_id}\r\n-----011000010111000001101001--"""
                            
                            headers = {
                                "accept": "application/json",
                                "content-type": "multipart/form-data; boundary=---011000010111000001101001",
                                "authorization": f"Bearer {API_KEY}"
                            }

                            response = requests.put(url, data=payload, headers=headers)
                            print(response)
                            time.sleep(5)

                            """moves a person from one position to another based on the overall score by calling a trigger (no it cant be done in the same api call)"""

                            url = f"https://app.loxo.co/api/{AGENCY_SLUG}/person_events"
                            payload = f"""-----011000010111000001101001\r\nContent-Disposition: form-data; name="person_event[activity_type_id]"\r\n\r\n{activity_type_id}\r\n-----011000010111000001101001\r\nContent-Disposition: form-data; name="person_event[person_id]"\r\n\r\n{person_id}\r\n-----011000010111000001101001\r\nContent-Disposition: form-data; name="person_event[job_id]"\r\n\r\n{job_id}\r\n-----011000010111000001101001--"""

                            headers = {
                                "accept": "application/json",
                                "content-type": "multipart/form-data; boundary=---011000010111000001101001",
                                "authorization": f"Bearer {API_KEY}"
                            }

                            response = requests.post(url, data=payload, headers=headers)
                            
                            # Mark email as processed in Gmail

                            """adds a processed tag in gmail so we dont fetch the same mail again"""

                            mark_email_as_processed_in_gmail(get_gmail_service(), message_id)
                            
                            f.write(f"✅ Automation Completed Successfully for {stream_name} (Score: {overall_score})\n")
                            f.flush()
                            
                            print(f"✅ Successfully processed: {stream_name} (Score: {overall_score})")
                            successful += 1

                        except Exception as e:
                            f.write(f"❌ Error processing {result.get('candidate_name', 'Unknown')}: {str(e)}\n")
                            f.flush()
                            print(f"❌ Error processing application for {result.get('candidate_name', 'Unknown')}: {e}")
                            failed += 1
                        
                        # Small delay between applications
                        time.sleep(10)
                    
                    # Write cycle summary
                    f.write(f"\n{'='*50}\n")
                    f.write(f"✅ Cycle #{cycle_count} completed at: {datetime.now()}\n")
                    f.write(f"📊 Results: {successful} successful, {failed} failed\n")
                    f.write(f"{'='*50}\n\n")
            
            print(f"\n✅ Cycle #{cycle_count} completed!")
            print(f"📊 Results: {successful} successful, {failed} failed")
            
            if choice == "1":
                break  # Exit after one cycle for single run
            
            print(f"⏰ Waiting 10 minutes before next cycle...")
            print(f"💤 Next cycle will start at: {(datetime.now() + timedelta(minutes=10)).strftime('%H:%M:%S')}")
            
            # Wait 10 minutes (600 seconds)
            time.sleep(120)
            
            """since while loop is not broken the next cycle is started after the above time"""

    except KeyboardInterrupt:
        print("\n🛑 Automation stopped by user")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")