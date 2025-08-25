import json
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

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Make sure the variable name matches your .env
client = OpenAI(api_key=OPENAI_API_KEY)

# Function to load job-specific prompts from the JSON file
def get_job_prompt(job_title):
    with open('job_promts.json', 'r') as file:
        job_prompts = json.load(file)
    
    # Perform a case-insensitive partial match on job title
    for job_name, job_data in job_prompts.items():
        if job_title.lower() in job_name.lower():  # Partial match, case insensitive
            return job_data["prompt"]  # Return the prompt for the matched job

    # If no match is found
    general = "You are an expert recruiter. Evaluate this candidate for the given job position."
    return general

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

# Function to evaluate candidate fit using LLM
def evaluate_candidate_with_llm(resume_text, clean_description, job_title):
    """Use OpenAI to evaluate candidate fit for the job"""
    
    # Get the job-specific prompt based on the job title
    job_prompt = get_job_prompt(job_title)
    
    if job_prompt is None:
        raise ValueError(f"Job prompt for '{job_title}' not found!")

    # Combine the job prompt with the resume and job description
    prompt = f"""
    You are an expert recruiter. Evaluate this candidate for the given job position.
    
    **Job Details:**
    Job Description: {clean_description}
    Primary Skills: Python, LangChain, LLM APIs, FAISS, Weaviate, Pinecone, and more

    **Candidate Profile:**
    Resume Content: {resume_text}

    **Job Evaluation Criteria:**
    {job_prompt}
    
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
            temperature=0.4,
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

# Example usage
# resume_text = "This candidate has 5 years of experience in Python development..."
# clean_description = "Shop Helper/Laborer responsible for lifting materials..."
# job_title = "Shop Helper/Laborer"

# evaluation_result = evaluate_candidate_with_llm(resume_text, clean_description, job_title)
# print(evaluation_result)
job_title = 'CNC Machine Operator'
resume_text = extract_text_from_pdf('Thomas-Walls (1).pdf')
print(resume_text)
test = get_job_prompt(job_title)
print(test)

clean_description = """

Bronwick, LLC finds top talent for companies that we admire.



Summary:

Our client is a premier Stone Countertop Fabricator in the Piedmont Triad region. They are family owned/operated, are highly rated by customers, and have an excellent company culture.

We are currently searching for a CNC Operator.

RESPONSIBILITIES:

Set up and operate Prussiani CNC Machine
Ensure accuracy and quality of finished products
Interpret blueprints and specifications
Collaborate with production team members for efficiency
REQUIREMENTS:

1+ Year Stone Countertops Experience (PREFERRED)
1+ Year CNC Experience (REQUIRED)
Positive Attitude/Team Player
COMPENSATION:

$18-$26/hr
PTO, Holidays, and Sick Pay
401k match
#IND1




"""
evaluation_result = evaluate_candidate_with_llm(resume_text, clean_description, job_title)
print(evaluation_result)