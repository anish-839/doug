from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from langchain_community.llms import OpenAI
import os
from dotenv import load_dotenv
import json
import time
import threading
import random
import requests
import sqlite3
import re, base64, tempfile
from typing import Tuple, Optional, Dict, Any
from datetime import datetime
import pdfplumber  # Add this library to extract text from PDFs
from bs4 import BeautifulSoup

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
API_KEY = os.getenv("LOXO_API")
AGENCY_SLUG = os.getenv("LOXO_AGENCY_SLUG")

# Initialize Twilio client for sending messages
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Load job-specific questions from JSON
with open('job_questions.json', 'r') as f:
    job_questions = json.load(f)

# Fixed questions
fixed_questions = [
    {"question": "Do you have any related qualifications?", "follow_up": None},
    {"question": "When can you start?", "follow_up": None},
    {"question": "Why are you interested in this role?", "follow_up": None}
]

# Initialize LLM (OpenAI in this case)
llm = OpenAI(api_key=OPENAI_API_KEY, temperature=0.7)

# Initialize Flask app
app = Flask(__name__)

class DatabaseManager:
    def __init__(self, db_path='whatsapp_bot.db'):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        """Initialize SQLite database with required tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # User states table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_states (
                    phone_number TEXT PRIMARY KEY,
                    step TEXT NOT NULL,
                    job_title TEXT,
                    current_question INTEGER DEFAULT 0,
                    responses TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Candidates table for storing phone number to person_id mapping
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone_number TEXT UNIQUE NOT NULL,
                    person_id INTEGER NOT NULL,
                    job_id INTEGER NOT NULL,
                    name TEXT,
                    email TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            print("Database initialized successfully")

    def get_user_state(self, phone_number):
        """Get user state from database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT step, job_title, current_question, responses 
                FROM user_states WHERE phone_number = ?
            ''', (phone_number,))
            
            result = cursor.fetchone()
            if result:
                step, job_title, current_question, responses_json = result
                responses = json.loads(responses_json) if responses_json else []
                return {
                    'step': step,
                    'job_title': job_title,
                    'current_question': current_question,
                    'responses': responses
                }
            return None

    def save_user_state(self, phone_number, state):
        """Save or update user state in database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            responses_json = json.dumps(state.get('responses', []))
            
            cursor.execute('''
                INSERT OR REPLACE INTO user_states 
                (phone_number, step, job_title, current_question, responses, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (
                phone_number,
                state.get('step'),
                state.get('job_title'),
                state.get('current_question', 0),
                responses_json
            ))
            conn.commit()

    def delete_user_state(self, phone_number):
        """Delete user state from database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM user_states WHERE phone_number = ?', (phone_number,))
            conn.commit()

    def get_candidate_info(self, phone_number):
        """Get person_id and job_id for a phone number"""
        # Clean phone number (remove whatsapp: prefix if present)
        clean_phone = phone_number.replace('whatsapp:', '').strip()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT person_id, job_id, name, email 
                FROM candidates WHERE phone_number = ? OR phone_number = ?
            ''', (phone_number, clean_phone))
            
            result = cursor.fetchone()
            if result:
                return {
                    'person_id': result[0],
                    'job_id': result[1],
                    'name': result[2],
                    'email': result[3]
                }
            return None

    def add_candidate(self, phone_number, person_id, job_id, name=None, email=None):
        """Add a new candidate to the database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO candidates 
                (phone_number, person_id, job_id, name, email)
                VALUES (?, ?, ?, ?, ?)
            ''', (phone_number, person_id, job_id, name, email))
            conn.commit()

# Initialize database manager
db = DatabaseManager()

def send_delayed_message(message, to_number, delay=None):
    """Send a message with realistic delay (no typing indicator)"""
    def send_with_delay():
        # Random delay between 2-6 seconds to simulate human response time
        if delay is None:
            delay_time = random.uniform(2, 6)
        else:
            delay_time = delay
        
        print(f"⏰ Waiting {delay_time:.1f} seconds before responding...")
        time.sleep(delay_time)
        
        # Send the actual message
        try:
            twilio_client.messages.create(
                body=message,
                from_=TWILIO_WHATSAPP_NUMBER,
                to=to_number
            )
            print(f"✅ Sent message to {to_number}: {message}")
        except Exception as e:
            print(f"❌ Error sending message: {e}")
    
    # Run in a separate thread to avoid blocking the webhook response
    thread = threading.Thread(target=send_with_delay)
    thread.daemon = True
    thread.start()

def add_human_touch_to_message(message):
    """Add slight variations to make messages feel more human"""
    variations = {
        "Thank you for answering all the questions!": [
            "Perfect! That's all the questions I have for you. Thank you for your time!",
            "Great! We've covered everything. Thanks so much for your responses!",
            "Excellent! That completes our screening. Thank you for participating!"
        ],
        "Thanks for responding!": [
            "Hi there! Thanks for getting back to us!",
            "Hello! Great to hear from you!",
            "Hey! Thanks for responding so quickly!"
        ]
    }
    
    # Check if message has variations available
    for key, options in variations.items():
        if key in message:
            return random.choice(options)
    
    return message

class JobScreeningAgent:
    def __init__(self, job_title, phone_number, existing_responses=None):
        self.job_title = job_title
        self.phone_number = phone_number
        self.questions = job_questions.get(self.job_title, [])
        self.questions.extend(fixed_questions)  # Add fixed questions
        self.current_question = 0
        self.responses = existing_responses or []

    def get_question(self):
        # Return the current question with some human-like variations
        if self.current_question < len(self.questions):
            base_question = self.questions[self.current_question]["question"]
            
            # Add some casual prefixes occasionally
            prefixes = [
                "",  # Most of the time, no prefix
                "Let me ask you - ",
                "I'd like to know - ",
                "Here's my next question: ",
                "Alright, "
            ]
            
            # 30% chance to add a prefix
            if random.random() < 0.3:
                prefix = random.choice(prefixes[1:])  # Exclude empty string
                return prefix + base_question.lower()
            
            return base_question
        else:
            return "Thank you for answering all the questions!"

    def get_follow_up(self):
        # Return the follow-up question if any
        if self.current_question < len(self.questions):
            return self.questions[self.current_question].get("follow_up", "")
        else:
            return ""

    def move_to_next_question(self):
        # Move to the next question
        if self.current_question < len(self.questions) - 1:
            self.current_question += 1
            return False  # Continue asking questions
        else:
            return True  # Stop if all questions have been answered

    def run(self, user_input):
        """Process user input and return next question or completion message"""
        self.responses.append(user_input)
        
        # Save state to database
        state = {
            'step': 'asking_questions',
            'job_title': self.job_title,
            'current_question': self.current_question,
            'responses': self.responses
        }
        db.save_user_state(self.phone_number, state)

        # Move to the next question
        if self.move_to_next_question():
            return add_human_touch_to_message("Thank you for answering all the questions!")

        # Get the next question and update state
        question = self.get_question()
        state['current_question'] = self.current_question
        db.save_user_state(self.phone_number, state)
        
        return question

    def evaluate_with_llm(self, responses):
        # Build a prompt to evaluate responses based on job-specific criteria
        prompt = f"""
        Evaluate the following candidate responses for the role '{self.job_title}'.
        Focus on their qualifications, skills, enthusiasm, and availability for the job.
        Provide a score (out of 10) for overall score, qualifications, enthusiasm, and availability.
        Additionally, provide one unified summary for the overall evaluation.

        Candidate responses:
        """

        for idx, response in enumerate(responses, 1):
            prompt += f"Q{idx}: {response}\n"

        prompt += "\nPlease provide structured feedback like this:\n"
        prompt += "Overall score: X\n"
        prompt += "Qualifications score: X\n"
        prompt += "Enthusiasm score: X\n"
        prompt += "Availability score: X\n"
        prompt += "Summary: (Feedback Summary)\n"

        # Send to LLM for evaluation using `invoke` and pass the prompt as a string
        evaluation = llm.invoke(prompt)

        # Assuming the LLM returns structured feedback, parse it to extract scores
        evaluation_data = self.extract_scores_from_evaluation(evaluation)

        return evaluation_data

    def extract_scores_from_evaluation(self, evaluation):
        # Initialize the structure for evaluation data
        evaluation_data = {
            "overall_score": 0,
            "qualifications_score": 0,
            "enthusiasm_score": 0,
            "availability_score": 0,
            "feedback": ""
        }

        # Split the raw evaluation string based on newlines
        lines = evaluation.split("\n")

        # Directly extract the scores by splitting the lines
        for line in lines:
            if line.startswith("Overall score:"):
                try:
                    evaluation_data["overall_score"] = int(line.split(":")[1].strip())
                except:
                    evaluation_data["overall_score"] = 0
            elif line.startswith("Qualifications score:"):
                try:
                    evaluation_data["qualifications_score"] = int(line.split(":")[1].strip())
                except:
                    evaluation_data["qualifications_score"] = 0
            elif line.startswith("Enthusiasm score:"):
                try:
                    evaluation_data["enthusiasm_score"] = int(line.split(":")[1].strip())
                except:
                    evaluation_data["enthusiasm_score"] = 0
            elif line.startswith("Availability score:"):
                try:
                    evaluation_data["availability_score"] = int(line.split(":")[1].strip())
                except:
                    evaluation_data["availability_score"] = 0
            elif line.startswith("Summary:"):
                evaluation_data["feedback"] = line.split("Summary:")[1].strip()

        return evaluation_data


@app.route('/whatsapp', methods=['POST'])
def whatsapp_reply():
    """Handle incoming WhatsApp messages from Twilio."""
    # Get the message from Twilio
    incoming_msg = request.form.get("Body")
    from_number = request.form.get("From")
    print(f"📩 {from_number} says: {incoming_msg}")

    # Return empty TwiML response immediately (to avoid timeout)
    resp = MessagingResponse()

    # Get user state from database
    user_state = db.get_user_state(from_number)

    # Check if user already has a conversation state
    if not user_state:
        # Check if candidate exists in database
        candidate_info = db.get_candidate_info(from_number)
        if not candidate_info:
            error_message = "Sorry, I couldn't find your information in our system. Please make sure you're using the phone number you provided during your job application."
            send_delayed_message(error_message, from_number, delay=3)
            return str(resp)
        
        # Initialize new conversation
        state = {'step': 'ask_job_title'}
        db.save_user_state(from_number, state)
        
        welcome_message = add_human_touch_to_message("Thanks for responding!")
        send_delayed_message(welcome_message, from_number, delay=2)
        
        # Send the job title request as a separate message
        job_request_message = "Before we get started, could you please specify the position you applied for?"
        send_delayed_message(job_request_message, from_number, delay=5)
        
        return str(resp)

    # Handle the step where user specifies the job title
    if user_state['step'] == 'ask_job_title':
        job_title = incoming_msg.strip().lower()  # Convert to lowercase for easier matching

        # Search job_questions.json for matching job title
        matched_job = None
        for job in job_questions.keys():
            if job_title in job.lower():  # Case-insensitive matching
                matched_job = job
                break

        if matched_job:
            # Initialize the agent for the matched job
            agent = JobScreeningAgent(matched_job, from_number)
            
            # Update state in database
            state = {
                'step': 'asking_questions',
                'job_title': matched_job,
                'current_question': 0,
                'responses': []
            }
            db.save_user_state(from_number, state)
            
            question = agent.get_question()
            
            # Add a confirmation message before starting questions
            confirmation = f"Perfect! I see you're interested in the {matched_job} position. Let me ask you a few questions to get to know you better."
            send_delayed_message(confirmation, from_number, delay=3)
            
            # Send first question with additional delay
            send_delayed_message(question, from_number, delay=6)
            
            return str(resp)
        else:
            error_message = "Hmm, I couldn't find a matching job title in our system. Could you please try specifying the position again? Maybe check the job posting for the exact title?"
            send_delayed_message(error_message, from_number, delay=3)
            return str(resp)

    # Handle completed conversations - ignore further messages
    if user_state['step'] == 'completed':
        print(f"🚫 Ignoring message from completed user {from_number}: {incoming_msg}")
        return str(resp)

    # Handle ongoing conversation
    if user_state['step'] == 'asking_questions':
        # Initialize agent with existing state
        agent = JobScreeningAgent(
            user_state['job_title'], 
            from_number, 
            user_state.get('responses', [])
        )
        agent.current_question = user_state.get('current_question', 0)

        # Add acknowledgment responses occasionally
        acknowledgments = ["Got it!", "Thanks for that info.", "Interesting!", "I see.", "Noted."]
        
        # 40% chance to send an acknowledgment before the next question
        if random.random() < 0.4:
            ack = random.choice(acknowledgments)
            send_delayed_message(ack, from_number, delay=1.5)

        # Run the agent with the incoming message
        chatbot_response = agent.run(incoming_msg)

        # Check if we have completed all questions
        if "Thank you" in chatbot_response:
            # Send completion message
            send_delayed_message(chatbot_response, from_number, delay=4)
            
            # Send a follow-up message about next steps
            followup_message = "Someone from our team will review your responses and get back to you soon. Have a great day!"
            send_delayed_message(followup_message, from_number, delay=8)
            
            # Mark conversation as completed
            state = user_state.copy()
            state['step'] = 'completed'
            db.save_user_state(from_number, state)
            
            # Run evaluation in background thread to avoid blocking
            def run_evaluation():
                time.sleep(2)  # Small delay before evaluation
                evaluation_result = agent.evaluate_with_llm(agent.responses)
                print(f"📊 Evaluation Result for {from_number}: {evaluation_result}")
                
                # Get candidate info from database
                candidate_info = db.get_candidate_info(from_number)
                if not candidate_info:
                    print(f"❌ No candidate info found for {from_number}")
                    return
                
                person_id = candidate_info['person_id']
                job_id = candidate_info['job_id']
                
                print(f"🔍 Processing evaluation for person_id: {person_id}, job_id: {job_id}")

                # Loxo integration
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

                print(f"📝 Current person description: {person_desc}")
                
                overall_score = evaluation_result['overall_score']
                qualifications_score = evaluation_result['qualifications_score']
                enthusiasm_score = evaluation_result['enthusiasm_score']
                availability_score = evaluation_result['availability_score']
                feedback = evaluation_result['feedback']

                person_desc += f"\n\nChatbot Summary: {feedback}\n\nOverall Score: {overall_score}\n\nQualifications Score: {qualifications_score}\n\nEnthusiasm Score: {enthusiasm_score}\n\nAvailability Score: {availability_score}"

                time.sleep(3)
                url = f"https://app.loxo.co/api/{AGENCY_SLUG}/people/{person_id}"
                payload = f"""-----011000010111000001101001\r\nContent-Disposition: form-data; name="job_id"\r\n\r\n{job_id}\r\n-----011000010111000001101001\r\nContent-Disposition: form-data; name="person[description]"\r\n\r\n{person_desc}\r\n-----011000010111000001101001--"""
                                    
                headers = {
                    "accept": "application/json",
                    "content-type": "multipart/form-data; boundary=---011000010111000001101001",
                    "authorization": f"Bearer {API_KEY}"
                }

                response = requests.put(url, data=payload, headers=headers)
                print(f"🔄 Loxo update response: {response.status_code}")
                
                # Clean up conversation state after evaluation is complete
                db.delete_user_state(from_number)
                print(f"🧹 Cleaned up conversation state for {from_number}")
            
            # Start evaluation in background thread
            eval_thread = threading.Thread(target=run_evaluation)
            eval_thread.daemon = True
            eval_thread.start()
        else:
            # Send the next question with realistic delay
            send_delayed_message(chatbot_response, from_number, delay=random.uniform(4, 7))

    return str(resp)


# Helper route to add candidates to database (for testing/setup)
@app.route('/add_candidate', methods=['POST'])
def add_candidate():
    """Add a candidate to the database"""
    data = request.json
    phone_number = data.get('phone_number')
    person_id = data.get('person_id')
    job_id = data.get('job_id')
    name = data.get('name')
    email = data.get('email')
    
    if not all([phone_number, person_id, job_id]):
        return jsonify({'error': 'Missing required fields: phone_number, person_id, job_id'}), 400
    
    try:
        db.add_candidate(phone_number, person_id, job_id, name, email)
        return jsonify({'message': 'Candidate added successfully'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == "__main__":
    print("🚀 WhatsApp Job Screening Bot is starting...")
    print("📱 Make sure to set up your environment variables:")
    print("   - TWILIO_ACCOUNT_SID")
    print("   - TWILIO_AUTH_TOKEN") 
    print("   - TWILIO_WHATSAPP_NUMBER")
    print("   - OPENAI_API_KEY")
    print("   - LOXO_API")
    print("   - LOXO_AGENCY_SLUG")
    print("\n🔗 Don't forget to update your Twilio webhook URL with your ngrok URL!")
    print("\n💾 Database initialized at: whatsapp_bot.db")
    app.run(port=5000, debug=False)