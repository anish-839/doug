#import os
import requests
from dotenv import load_dotenv
from twilio.rest import Client
import json
import pdfplumber  # Add this library to extract text from PDFs
from bs4 import BeautifulSoup
from openai import OpenAI
import json
#import re

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

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

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

def _parse_name_and_title(html: Optional[str], text: Optional[str], subject: str) -> Tuple[Optional[str], Optional[str]]:
    def parse_lines(lines):
        cand, title = None, None
        for i, line in enumerate(lines):
            if line.lower().endswith("applied"):
                cand = line[: -len("applied")].strip()
                if i + 1 < len(lines):
                    nxt = lines[i + 1]
                    title = re.split(r"[•,|-]", nxt)[0].strip()
                break
        return cand, title

    if html:
        soup = BeautifulSoup(html, "html.parser")
        lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
        cand, title = parse_lines(lines)
        if cand or title:
            return cand, title

    if text:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        cand, title = parse_lines(lines)
        if cand or title:
            return cand, title

    jt = None
    m = re.search(r"New application for\s*(.*?)(?:,|$)", subject, re.I)
    if m:
        jt = m.group(1).strip()

    return None, jt

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

# ---------- Public function ----------


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

def fetch_application(query: str, download_dir: Optional[str] = None):
    """
    Returns:
      {
        'candidate_name': str|None,
        'job_title': str|None,
        'resume_path': str|None,
        'resume_filename': str|None,
        'message_id': str|None,
        'subject': str
      }
    """
    service = get_gmail_service()

    resp = service.users().messages().list(userId="me", q=query, maxResults=1).execute()
    msgs = resp.get("messages", [])
    if not msgs:
        return {'candidate_name': None, 'job_title': None, 'resume_path': None,
                'resume_filename': None, 'message_id': None, 'subject': None}

    msg = service.users().messages().get(userId="me", id=msgs[0]["id"], format="full").execute()
    subject = _get_subject(msg)
    html, text = _get_html_and_text(msg.get("payload", {}))
    candidate_name, job_title = _parse_name_and_title(html, text, subject)
    resume_path, resume_filename = _download_first_resume_attachment(service, msg, download_dir)

    return {
        'candidate_name': candidate_name,
        'job_title': job_title,
        'resume_path': resume_path,
        'resume_filename': resume_filename,
        'message_id': msg.get('id'),
        'subject': subject
    }

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

# Function to find job by title
def find_job_by_title(title):
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
        
        if title.lower() in job.get("title", "").lower():
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
Job Title: AI Developer
Location: Remote / Bangalore / Hybrid
Salary: ₹18–25 LPA (Based on experience)
Job Type: Full-time
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
            model="gpt-3.5-turbo",
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


def send_whatsapp_message(to_number):
    """
    Sends a WhatsApp message using the Twilio API.

    :param to_number: The recipient's verified WhatsApp number (in the format: '+91-9833944247')
    :return: SID of the sent message
    """
    
    # Remove hyphens and format the number correctly
    formatted_number = 'whatsapp:' + to_number.replace('-', '').replace(' ', '')

    message = twilio.messages.create(
        body="Your Application was Submitted! Please be patient, your resume is being processed. The team will get in touch with you. If you have any further questions, please feel free to respond here.",
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
    print(f"📝 Evaluation Result: {evaluation_result}")

    return job_description , evaluation_result



# Example usage
if __name__ == "__main__":
    #candidate_name_or_email = "Anish Patil" 
    #job_title = "Test Job"

    query = 'subject:"New application for Test Job, Bow, NH" has:attachment newer_than:7d'
    # Set your folder here (Windows example). Create if it doesn't exist.
    download_folder = r"C:\Users\LENOVO\Desktop\work_please\resume"
    result = fetch_application(query, download_dir=download_folder)

    resume_path = result['resume_path']  # this is the real saved file path
    if not resume_path or not os.path.exists(resume_path):
        raise FileNotFoundError("Resume file not found")

    resume_text = extract_text_from_pdf(resume_path)
    

    #resume_text = extract_text_from_pdf("ANISH_PATIL_CV.pdf")
    print(resume_text)

    EXPECTED_EMAIL = extract_email(resume_text)
    print(EXPECTED_EMAIL)

    candidate_name_or_email = result['candidate_name'] 
    job_title = result['job_title']

    print(f"🔍 Looking for candidate: {candidate_name_or_email}")
    person, person_id, phone_number = search_person_by_name(candidate_name_or_email)
    if not person:
        print("❌ Candidate not found.")
        exit()

    print(f"🔍 sending automated message to: {phone_number}")

    

    send_whatsapp_message(phone_number)



    print(f"🔍 Looking for job: {job_title}")
    job, job_id = find_job_by_title(job_title)
    if not job:
        print("❌ Job not found.")
        exit()

    #resume_id = 82078278  # Replace with the actual resume ID
    print("🔗 Processing candidate's resume and job description...")
    #job_description = process_candidate_resume(person_id, resume_id, job_id)
    
    job_description, evaluation_result = process_candidate_resume(job_id)
    #print(resume_file)
    print(job_id)
    print(person)
    #print(EXPECTED_EMAIL)
    print(phone_number)
    #apply_for_job(job_id, person, email, phone, resume_filename)
    #apply_for_job(job_id, person, EXPECTED_EMAIL, phone_number, resume_file)

    overall_score = evaluation_result['overall_score']

    ah_pronoun = "AI Accepted" if overall_score > 60 else "AI Rejected"


    
    

    url = "https://app.loxo.co/api/bronwick-recruiting-and-staffing/jobs/3372115/apply"
    #url = f"https://app.loxo.co/api/{agency_slug}/jobs/{job_id}/apply"
    files = { "resume": (resume_path, open(resume_path, "rb"), "application/pdf") }
    payload = {
        "name": f"{person}",
        "phone": f"{phone_number}",
        "email": f"{EXPECTED_EMAIL}",
        "pronoun_id": f"{overall_score}",
        "other_pronoun": f"{ah_pronoun}",
    }
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {API_KEY}"
    }

    response = requests.post(url, data=payload, files=files, headers=headers)

    #print(response.text)

    

    # Call the function to extract the email
    email_id = extract_email(resume_text)
    print(f"Extracted Email ID: {email_id}")

    #print(f"Extracted Email ID: apanishpatil839@gmail.com")

    if overall_score > 60:
        ah_tag = "AI Accepted"
    else:
        ah_tag = "AI Rejected"

    

    #import requests

    url = f"https://app.loxo.co/api/bronwick-recruiting-and-staffing/people/{person_id}"

    # Create the payload with dynamic variables
    payload = f"""-----011000010111000001101001\r\nContent-Disposition: form-data; name="job_id"\r\n\r\n{job_id}\r\n-----011000010111000001101001\r\nContent-Disposition: form-data; name="person[raw_tags][]"\r\n\r\n{ah_tag}\r\n-----011000010111000001101001--"""

    # Define the headers
    headers = {
        "accept": "application/json",
        "content-type": "multipart/form-data; boundary=---011000010111000001101001",
        "authorization": f"Bearer {API_KEY}"
    }

    # Send the PUT request
    response = requests.put(url, data=payload, headers=headers)

    # Print the response text
    #print(response.text)


    #print(response.text)

    #get_job_applications(job_id)
    #save_job_description(job_title, job_description)
    #print(resume_text)


    #print(job_description)
