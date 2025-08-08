from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv
from openai import OpenAI
import pandas as pd
import os
from collections import defaultdict
#import full_automation


# === SETUP ===
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Make sure the variable name matches your .env
client = OpenAI(api_key=OPENAI_API_KEY)


job_desc = """
Job Title: AI DeveloperLocation: Remote / Bangalore / HybridSalary: ₹18–25 LPA (Based on experience)Job Type: Full-timeJob Description:We are seeking a talented and highly motivated AI Developer to join our engineering team to build next-generation AI-driven applications. As part of this role, you will work on designing and deploying intelligent agents and workflows powered by large language models (LLMs), leveraging tools like LangChain, Python, and modern vector databases.You’ll collaborate closely with product managers and designers to develop intelligent systems that solve real-world problems in automation, customer interaction, data processing, and beyond.Key Responsibilities:
Design, develop, and maintain LLM-powered applications using frameworks like LangChain, LlamaIndex, etc.
Integrate LLMs (OpenAI, Anthropic, etc.) with custom workflows using Python.
Build and optimize retrieval-augmented generation (RAG) pipelines with FAISS, Weaviate, or Pinecone.
Deploy AI agents and tools for task automation and intelligent decision-making.
Fine-tune and prompt-engineer large language models for domain-specific use cases.
Collaborate with backend teams to integrate AI features into production systems.
Monitor and improve the performance and safety of AI outputs.
Primary Skills:
Strong proficiency in Python

Hands-on experience with LangChain or similar LLM orchestration tools
Familiarity with LLM APIs (OpenAI, Cohere, Mistral, etc.)
Experience with vector databases (FAISS, Pinecone, Weaviate)
Understanding of prompt engineering and model fine-tuning
REST APIs and integration know-how
Good to Have:
Experience with FastAPI, Flask, or other Python web frameworks
Familiarity with Docker, Kubernetes, or cloud deployment (AWS/GCP)
Knowledge of machine learning fundamentals and NLP techniques

Contributions to open-source LLM tools or personal AI projects
About Us:We are a fast-growing AI-first startup building cutting-edge solutions for enterprise automation and knowledge retrieval. Join us if you're excited about working with some of the most exciting tech in the world and want to make real impact with AI.




"""
# MEMORY_LIMIT = 5  # Number of previous exchanges remembered
# user_memory = defaultdict(list)  # Stores per-user memory


app = Flask(__name__)

# === UTILITY FUNCTIONS ===

# def find_job_description(msg):
#     msg_lower = msg.lower()
#     for _, row in jobs_df.iterrows():
#         if row['Title'].lower() in msg_lower:
#             return row['Description']
#     return None
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

    # Use last LLM + user message as context
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
Based on the candidate's message, only respond with relevant information that is explicitly or implicitly asked.
If the message mentions:
- Salary → return salary info only.
- Location → return job location only.
- Skills or requirements → return only skills.
If the query is general (e.g. "am I eligible?" or "tell me more"), give a helpful summary.

Do not list everything unless asked. Keep your response brief and on-point.
"""
    })

    try:
        completion = client.chat.completions.create(
            model="gpt-4",
            #model="gpt-3.5-turbo",  # or "gpt-4"
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

# === TWILIO ROUTE ===
user_job_context = {}

@app.route("/sms", methods=['POST'])
def sms_reply():
    msg = request.form.get("Body")
    from_number = request.form.get("From")
    print(f"📩 {from_number} says: {msg}")

    # Try to find job in this message
    #job_desc 

    if job_desc:
        reply = ask_openai(from_number, msg, job_desc)
    else:
        reply = "Sorry, I couldn't find a matching job for your query. Please mention the job title."

    response = MessagingResponse()
    response.message(reply)
    return str(response)
    

if __name__ == "__main__":
    app.run(port=5000, debug=True)
