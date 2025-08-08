import os
import requests
from dotenv import load_dotenv
from twilio.rest import Client
import json
import pdfplumber  # Add this library to extract text from PDFs
from bs4 import BeautifulSoup
from openai import OpenAI
import json

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

EXPECTED_EMAIL = "runningoutofuniqueemail@gmail.com"  # Hardcoded for now

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
def download_resume(person_id, resume_id):
    url = f"https://app.loxo.co/api/{AGENCY_SLUG}/people/{person_id}/resumes/{resume_id}/download"
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json"
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        resume_filename = f"resume_{person_id}_{resume_id}.pdf"
        with open(resume_filename, "wb") as f:
            f.write(response.content)
        print(f"✅ Resume downloaded successfully as {resume_filename}")
        return resume_filename
    else:
        print(f"❌ Failed to download the resume. Status code: {response.status_code}")
        return None

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


def apply_for_job(job_id, name, phone, email, resume_filename):
    
    url = f"https://app.loxo.co/api/{AGENCY_SLUG}/jobs/{job_id}/apply"
    
    # Prepare the resume file for upload
    with open(resume_filename, "rb") as resume_file:
        files = { 
            "resume": (resume_filename, resume_file, "application/pdf")
        }

        # Prepare the payload with the required candidate info
        payload = {
            "name": name,          # Candidate's name
            "phone": phone,        # Candidate's phone number
            "email": email         # Candidate's email address
        }

        # Prepare headers with authorization
        headers = {
            "accept": "application/json",
            "authorization": f"Bearer {API_KEY}"
        }

        # Send the POST request to apply for the job
        response = requests.post(url, data=payload, files=files, headers=headers)

    # Check the response status
    if response.status_code == 200:
        print("✅ Job application submitted successfully!")
        return response.json()  # Return the response data if needed
    else:
        print(f"❌ Failed to apply for the job. Status code: {response.status_code}")
        print(f"Response: {response.text}")  # Print out the error response for debugging
        return None

def apply_for_job(job_id, name, email, phone, resume_filename):
    url = f"https://app.loxo.co/api/{AGENCY_SLUG}/jobs/{job_id}/apply"
    
    headers = {
        "accept": "application/json",
        "content-type": "multipart/form-data",
        "authorization": f"Bearer {API_KEY}"
    }

    # Open the resume file in binary mode to send as part of the POST request
    try:
        with open(resume_filename, "rb") as resume_file:
            files = {
                "email": (None, email),  # Email of the candidate
                "name": (None, name),    # Name of the candidate
                "phone": (None, phone),  # Phone number of the candidate
                "resume": (resume_filename, resume_file, "application/pdf")  # The resume file
            }

            # Send the POST request
            response = requests.post(url, headers=headers, files=files)

        # Check if the response was successful
        if response.status_code == 200:
            print("✅ Job application submitted successfully!")
            print(f"Response Data: {response.json()}")  # Print the response to verify submission
            return response.json()  # Return the response data if needed
        else:
            print(f"❌ Failed to apply for the job. Status code: {response.status_code}")
            print(f"Response: {response.text}")  # Print out the detailed error response for debugging
            return None
    except Exception as e:
        print(f"Error during file upload: {str(e)}")
        return None



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




# Main function to process candidate resume
def process_candidate_resume(person_id, resume_id, job_id):
    # Download the resume
    resume = download_resume(person_id, resume_id)
    if not resume:
        print("❌ Failed to download resume.")
        return
    
    # Extract text from the downloaded resume
    resume_text = extract_text_from_pdf(resume)
    print(resume_text)
    if not resume_text:
        print("❌ Failed to extract text from resume.")
        return
    
    # Retrieve the job description
    job_description = retrieve_job_description(job_id)

    # Send the resume text and job description to LLM for evaluation
    evaluation_result = evaluate_candidate_with_llm(resume_text, job_description)
    print(f"📝 Evaluation Result: {evaluation_result}")

    return job_description , resume, evaluation_result



# Example usage
if __name__ == "__main__":
    candidate_name_or_email = "Anish Patil" 
    job_title = "Test Job"

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

    resume_id = 82078278  # Replace with the actual resume ID
    print("🔗 Processing candidate's resume and job description...")
    #job_description = process_candidate_resume(person_id, resume_id, job_id)
    
    job_description, resume_file, evaluation_result = process_candidate_resume(226713500, resume_id, job_id)
    print(resume_file)
    print(job_id)
    print(person)
    print(EXPECTED_EMAIL)
    print(phone_number)
    #apply_for_job(job_id, person, email, phone, resume_filename)
    #apply_for_job(job_id, person, EXPECTED_EMAIL, phone_number, resume_file)

    overall_score = evaluation_result['overall_score']


    if overall_score > 60:
    

        url = "https://app.loxo.co/api/bronwick-recruiting-and-staffing/jobs/3372115/apply"

        files = { "resume": ("ANISH_PATIL_CV.pdf", open("ANISH_PATIL_CV.pdf", "rb"), "application/pdf") }
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

    #get_job_applications(job_id)
    #save_job_description(job_title, job_description)
    #print(resume_text)

    #print(job_description)