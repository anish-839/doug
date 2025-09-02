import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI
import logging

# =============================
# 🔑 Setup
# =============================
load_dotenv()
API_KEY = os.getenv("LOXO_API")
AGENCY_SLUG = os.getenv("LOXO_AGENCY_SLUG")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

DOWNLOAD_DIR = "rec"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# OpenAI client
client = OpenAI(api_key=OPENAI_KEY)

# =============================
# Loxo API Helpers
# =============================
def get_person_events(job_id):
    url = f"https://app.loxo.co/api/{AGENCY_SLUG}/person_events"
    params = {
        "activity_type_ids[]": 634029,  # audio upload activity type
        "job_ids[]": job_id
    }
    headers = {"accept": "application/json", "authorization": f"Bearer {API_KEY}"}

    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code == 200:
        return resp.json().get("person_events", [])
    else:
        logging.warning(f"Failed to fetch person_events for job {job_id}: {resp.status_code}")
        return []

def download_document(person_event_id, doc_id, filename):
    url = f"https://app.loxo.co/api/{AGENCY_SLUG}/person_events/{person_event_id}/documents/{doc_id}/download"
    headers = {"authorization": f"Bearer {API_KEY}"}
    local_path = os.path.join(DOWNLOAD_DIR, filename)

    resp = requests.get(url, headers=headers, stream=True)
    if resp.status_code == 200:
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        logging.info(f"Downloaded {filename}")
        return local_path
    else:
        logging.warning(f"Failed to download document {doc_id}: {resp.status_code}")
        return None

def get_person_details(person_id):
    url = f"https://app.loxo.co/api/{AGENCY_SLUG}/people/{person_id}"
    headers = {"accept": "application/json", "authorization": f"Bearer {API_KEY}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        person_data = resp.json()
        person_desc = person_data.get("description", "")
        if person_desc:
            person_desc = BeautifulSoup(person_desc, "html.parser").get_text()
        return person_desc
    else:
        logging.warning(f"Failed to fetch person {person_id}: {resp.status_code}")
        return ""

def update_person_description(person_id, new_desc):
    url = f"https://app.loxo.co/api/{AGENCY_SLUG}/people/{person_id}"
    headers = {"authorization": f"Bearer {API_KEY}"}
    payload = {"person[description]": new_desc}
    resp = requests.put(url, data=payload, headers=headers)
    if resp.status_code == 200:
        logging.info(f"✅ Updated description for person {person_id}")
    else:
        logging.warning(f"❌ Failed to update person {person_id}: {resp.status_code} {resp.text}")

# =============================
# AI Helpers
# =============================
def transcribe_and_summarize(audio_file_path):
    try:
        # Step 1: Transcription
        with open(audio_file_path, "rb") as audio_file:
            transcription_resp = client.audio.transcriptions.create(
                model="gpt-4o-transcribe",
                file=audio_file,
                language="en"
            )
        transcription = transcription_resp.text
        logging.info(f"Transcription complete for {audio_file_path}")

        # Step 2: Summarization
        summary_resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Summarize the following text"},
                {"role": "user", "content": transcription}
            ],
            max_tokens=150
        )
        summary = summary_resp.choices[0].message.content.strip()
        logging.info(f"Summary: {summary[:100]}...")
        return summary
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None
