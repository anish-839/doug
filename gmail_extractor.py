import os, re, base64, tempfile
from typing import Tuple, Optional, Dict, Any
from datetime import datetime
from bs4 import BeautifulSoup

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

import pdfplumber  # Add this library to extract text from PDFs
#from bs4 import BeautifulSoup

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

# ---------- Example run ----------

if __name__ == "__main__":
    query = 'subject:"New application for Test Job, Bow, NH" has:attachment newer_than:7d'
    # Set your folder here (Windows example). Create if it doesn't exist.
    download_folder = r"C:\Users\LENOVO\Desktop\work_please\resume"
    result = fetch_application(query, download_dir=download_folder)

    #base_dir = r"C:\Users\LENOVO\Desktop\work_please\resume"
    resume_title = result['resume_filename']  # original filename
    
    resume_path = result['resume_path']  # this is the real saved file path
    if not resume_path or not os.path.exists(resume_path):
        raise FileNotFoundError("Resume file not found")

    resume_text = extract_text_from_pdf(resume_path)
    
    #resume_text = extract_text_from_pdf(resume_path)

    candidate_name_or_email = result['candidate_name'] 
    job_title = result['job_title']
    #resume_title = result['resume_filename']
    #resume_text = extract_text_from_pdf(r"C:\Users\LENOVO\Desktop\work_please\resume\{resume_title}")
    print(candidate_name_or_email)
    print(resume_title)
    print(job_title)
    print(resume_text)
    print(result)


url = "https://app.loxo.co/api/bronwick-recruiting-and-staffing/jobs/3372115/apply"

    files = { "resume": (resume_path, open(resume_path, "rb"), "application/pdf") }
    payload = {
        "name": f"{person}",
        "phone": f"{phone_number}",
        "email": f"{EXPECTED_EMAIL}"
    }
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {API_KEY}"
    }

    response = requests.post(url, data=payload, files=files, headers=headers)

    print(response.text)