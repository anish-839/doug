from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv
from openai import OpenAI
import pandas as pd
import os
from collections import defaultdict
import sqlite3
#import full_automation


# === SETUP ===
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Make sure the variable name matches your .env
client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)
user_job_context = {}

checkpoint_memory = {}  # user_id → last context pair (LLM + user message)

def get_job_description(job_title):
    """Fetches the job description for a given job title."""
    conn = sqlite3.connect('job_descriptions.db')
    cursor = conn.cursor()
    job_title = job_title.lower()
    
    # Query the database for the job description
    cursor.execute("SELECT job_description FROM job_descriptions WHERE job_title=?", (job_title,))
    row = cursor.fetchone()
    
    conn.close()
    
    if row:
        return row[0]  # Return job description text
    else:
        return "Job description not found."

def ask_openai(user_id, candidate_msg, job_desc):
    messages = [{"role": "system", "content": "You are a helpful recruiter bot."}]

    # Use last LLM + user message as context, if exists
    if user_id in checkpoint_memory:
        last_llm_msg, last_user_msg = checkpoint_memory[user_id]
        messages.append({"role": "user", "content": last_llm_msg})
        messages.append({"role": "user", "content": last_user_msg})

    # Now add the current user message
    messages.append({
        "role": "user",
        "content": f"""
Job Description:
{job_desc}

Candidate Message:
{candidate_msg}

Your task:
Based on the candidate's message, respond with relevant information. If the message mentions:
- Salary → return salary info only.
- Location → return job location only.
- Skills or requirements → return only skills.
If the query is general (e.g. "am I eligible?" or "tell me more"), give a helpful summary based on the job description.

Keep your response brief and on-point, and do not repeat the job description unless explicitly asked.
"""
    })

    try:
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=messages,
            temperature=0.7,
            max_tokens=512
        )
        reply = completion.choices[0].message.content.strip()
    except Exception as e:
        print("❌ OpenAI Error:", str(e))
        reply = "Sorry, I'm having trouble processing your request right now."

    # Update checkpoint with latest LLM + user message
    checkpoint_memory[user_id] = (reply, candidate_msg)

    return reply



@app.route("/sms", methods=['POST'])
def sms_reply():
    msg = request.form.get("Body")
    from_number = request.form.get("From")
    print(f"📩 {from_number} says: {msg}")

    # Check if this is the first message (user has not provided job title yet)
    if from_number not in user_job_context:
        # Static first message asking for the job title
        reply = "Thanks for reaching out! Before we proceed, please provide the job title you are referring to."
        user_job_context[from_number] = {"waiting_for_job_title": True}  # Mark that the user is being asked for job title
    else:
        # If the user has already provided the job title
        if "waiting_for_job_title" in user_job_context[from_number]:
            # The user has sent a job title
            job_title = msg.strip()  # Assuming the user sends the job title in response to the static message
            job_desc = get_job_description(job_title)  # Fetch the job description for this job title

            if job_desc:
                # Store job title and description in the user context
                user_job_context[from_number] = {
                    "job_title": job_title,
                    "job_description": job_desc
                }
                reply = f"Great! You've mentioned {job_title}. How can I help you with this job description?"
            else:
                reply = "Sorry, I couldn't find a matching job for your query. Please mention a valid job title."
                user_job_context[from_number]["waiting_for_job_title"] = True  # Still waiting for job title
        else:
            # If the job title is already stored, process the query with the job description
            job_desc = user_job_context[from_number]["job_description"]

            # Pass the query to OpenAI for processing
            reply = ask_openai(from_number, msg, job_desc)

    # Respond back to the user with the message
    response = MessagingResponse()
    response.message(reply)
    return str(response)



if __name__ == "__main__":
    app.run(port=5000, debug=True)