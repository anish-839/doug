from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from langchain_openai import OpenAI
import os
from dotenv import load_dotenv
import json
import time
import threading
import random
import requests
import redis
from celery import Celery
import pdfplumber
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any
from datetime import datetime, timezone

load_dotenv()

# Environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_NUMBER")  # Your Twilio SMS phone number
API_KEY = os.getenv("LOXO_API")
AGENCY_SLUG = os.getenv("LOXO_AGENCY_SLUG")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Loxo API setup
BASE = f"https://app.loxo.co/api/{AGENCY_SLUG}"
HEADERS = {
    "accept": "application/json",
    "authorization": f"Bearer {API_KEY}"
}

# Initialize Redis client
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Initialize Celery for async processing
celery_app = Celery(
    'lang_graph_sms',  # Updated name
    broker=REDIS_URL,
    backend=REDIS_URL
)

# Celery configuration
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_routes={
        'lang_graph_sms.send_delayed_message': {'queue': 'sms_queue'},
        'lang_graph_sms.process_evaluation': {'queue': 'evaluation_queue'},
        'lang_graph_sms.update_loxo': {'queue': 'loxo_queue'}
    },
    # Windows-specific settings
    worker_pool='solo',  # Use solo pool for Windows
    task_always_eager=False,
    task_eager_propagates=True
)

# Initialize Twilio client and LLM
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
llm = OpenAI(api_key=OPENAI_API_KEY, temperature=0.7)
app = Flask(__name__)

# Load job-specific questions from JSON (cached in Redis)
def load_job_questions():
    """Load job questions and cache in Redis"""
    cached_questions = redis_client.get("job_questions")
    if cached_questions:
        return json.loads(cached_questions)
    
    try:
        with open('job_questions.json', 'r') as f:
            job_questions = json.load(f)
        
        # Cache for 1 hour
        redis_client.setex("job_questions", 3600, json.dumps(job_questions))
        return job_questions
    except FileNotFoundError:
        # Return sample questions for testing
        sample_questions = {
            "Software Engineer": [
                {"question": "How many years of programming experience do you have?", "follow_up": None},
                {"question": "Which programming languages are you most comfortable with?", "follow_up": None}
            ],
            "Marketing Manager": [
                {"question": "How many years of marketing experience do you have?", "follow_up": None},
                {"question": "Which digital marketing platforms have you used?", "follow_up": None}
            ],
            "HR Manager": [
                {"question": "How many years of HR experience do you have?", "follow_up": None},
                {"question": "What HR systems and tools are you familiar with?", "follow_up": None}
            ]
        }
        redis_client.setex("job_questions", 3600, json.dumps(sample_questions))
        return sample_questions

# Fixed questions
FIXED_QUESTIONS = [
    {"question": "Do you have any related qualifications?", "follow_up": None},
    {"question": "When can you start?", "follow_up": None},
    {"question": "Why are you interested in this role?", "follow_up": None}
]

def search_person_by_email(email):
    """Search for person by email in Loxo API"""
    url = f"{BASE}/people?query={email}&per_page=5"
    
    print(f"Searching Loxo API: {url}")
    resp = requests.get(url, headers=HEADERS)
    print(f"Status Code: {resp.status_code}")
    
    if resp.status_code != 200:
        print(f"API Error: {resp.status_code}")
        return None
    
    data = resp.json()
    total_count = data.get("total_count", 0)
    print(f"Total people found: {total_count}")
    
    people = data.get("people", [])
    
    if not people:
        print("No people found.")
        return None
    
    for person in people:
        print(f"Checking person: {person['name']}")
        
        # Check emails
        for email_obj in person.get('emails', []):
            person_email = email_obj.get('value')
            if person_email and person_email.lower() == email.lower():
                print(f"Found matching candidate: {person['name']}")
                person_id = person.get('id')
                phone_numbers = person.get('phones', [])
                phone_number = phone_numbers[0].get('value') if phone_numbers else None
                print(f"Phone number: {phone_number}")
                person_name = person.get('name')
                
                return person_name, person_id, phone_number
    
    print(f"No person found with email: {email}")
    return None

class RedisStateManager:
    """Manage user conversation state in Redis"""
    
    def __init__(self, redis_client):
        self.redis = redis_client
        self.state_ttl = 3600  # 1 hour TTL for user states
    
    def get_user_state(self, phone_number: str) -> Optional[Dict]:
        """Get user state from Redis"""
        state_key = f"user_state:{phone_number}"
        state_data = self.redis.get(state_key)
        
        if state_data:
            return json.loads(state_data)
        return None
    
    def set_user_state(self, phone_number: str, state: Dict):
        """Set user state in Redis with TTL"""
        state_key = f"user_state:{phone_number}"
        self.redis.setex(state_key, self.state_ttl, json.dumps(state))
    
    def update_user_state(self, phone_number: str, updates: Dict):
        """Update specific fields in user state"""
        current_state = self.get_user_state(phone_number) or {}
        current_state.update(updates)
        self.set_user_state(phone_number, current_state)
    
    def delete_user_state(self, phone_number: str):
        """Delete user state from Redis"""
        state_key = f"user_state:{phone_number}"
        self.redis.delete(state_key)
    
    def extend_user_state_ttl(self, phone_number: str):
        """Extend TTL for active conversations"""
        state_key = f"user_state:{phone_number}"
        self.redis.expire(state_key, self.state_ttl)

# Initialize state manager
state_manager = RedisStateManager(redis_client)

# Async tasks using Celery
@celery_app.task(bind=True, max_retries=3)
def send_delayed_message(self, message, to_number, delay=None):
    """Send SMS message with delay - async task"""
    try:
        if delay:
            time.sleep(delay)
        else:
            # Random delay between 2-6 seconds
            delay_time = random.uniform(2, 6)
            time.sleep(delay_time)
        
        # Clean phone number (remove any prefixes)
        clean_number = to_number.replace('whatsapp:', '').strip()
        
        # Send the SMS message
        message_instance = twilio_client.messages.create(
            body=message,
            from_=TWILIO_PHONE_NUMBER,
            messaging_service_sid="MG1eb710ef78a892f29764c8f3d9698e1f",
            to=clean_number
        )
        
        print(f"Sent SMS to {clean_number}: {message}")
        return {"status": "sent", "sid": message_instance.sid}
        
    except Exception as e:
        print(f"Error sending SMS: {e}")
        # Retry with exponential backoff
        raise self.retry(countdown=60 * (2 ** self.request.retries))

@celery_app.task(bind=True, max_retries=2)
def process_evaluation(self, phone_number, responses, job_title, person_id):
    """Process LLM evaluation - async task"""
    try:
        # Build evaluation prompt
        prompt = f"""
        Evaluate the following candidate responses for the role '{job_title}'.
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
        
        # Get LLM evaluation
        evaluation = llm.invoke(prompt)
        evaluation_data = extract_scores_from_evaluation(evaluation)
        
        # Trigger Loxo update if person_id exists
        if person_id:
            update_loxo.delay(person_id, evaluation_data)
        else:
            print(f"Warning: No person_id for evaluation, skipping Loxo update")
        
        return evaluation_data
        
    except Exception as e:
        print(f"Error in evaluation: {e}")
        raise self.retry(countdown=30 * (2 ** self.request.retries))

@celery_app.task(bind=True, max_retries=3)
def update_loxo(self, person_id, evaluation_data):
    """Update Loxo with evaluation results - async task"""
    try:
        # Skip if no API credentials for testing
        if not API_KEY or not AGENCY_SLUG:
            print(f"Skipping Loxo update - no API credentials configured")
            return {"status": "skipped", "reason": "no_credentials"}
        
        # Get current person data
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
        
        # Append evaluation results
        overall_score = evaluation_data['overall_score']
        qualifications_score = evaluation_data['qualifications_score']
        enthusiasm_score = evaluation_data['enthusiasm_score']
        availability_score = evaluation_data['availability_score']
        feedback = evaluation_data['feedback']
        
        person_desc += f"\n\nSMS Bot Summary: {feedback}\n\n"
        person_desc += f"Overall Score: {overall_score}\n"
        person_desc += f"Qualifications Score: {qualifications_score}\n"
        person_desc += f"Enthusiasm Score: {enthusiasm_score}\n"
        person_desc += f"Availability Score: {availability_score}"
        
        # Update person in Loxo
        payload = f"""-----011000010111000001101001\r\nContent-Disposition: form-data; name="person[description]"\r\n\r\n{person_desc}\r\n-----011000010111000001101001--"""
        
        headers = {
            "accept": "application/json",
            "content-type": "multipart/form-data; boundary=---011000010111000001101001",
            "authorization": f"Bearer {API_KEY}"
        }
        
        response = requests.put(url, data=payload, headers=headers)
        
        if response.status_code == 200:
            print(f"Successfully updated Loxo for person {person_id}")
            return {"status": "success", "person_id": person_id}
        else:
            print(f"Loxo update failed: {response.status_code}")
            raise Exception(f"Loxo API error: {response.status_code}")
            
    except Exception as e:
        print(f"Error updating Loxo: {e}")
        raise self.retry(countdown=60 * (2 ** self.request.retries))

def extract_scores_from_evaluation(evaluation):
    """Extract scores from LLM evaluation response"""
    evaluation_data = {
        "overall_score": 0,
        "qualifications_score": 0,
        "enthusiasm_score": 0,
        "availability_score": 0,
        "feedback": ""
    }
    
    lines = evaluation.split("\n")
    
    for line in lines:
        line = line.strip()
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
    
    for key, options in variations.items():
        if key in message:
            return random.choice(options)
    
    return message

class JobScreeningAgent:
    def __init__(self, job_title, phone_number):
        self.job_title = job_title
        self.phone_number = phone_number
        job_questions = load_job_questions()
        self.questions = job_questions.get(self.job_title, [])
        self.questions.extend(FIXED_QUESTIONS)
        
        # Get current state from Redis
        state = state_manager.get_user_state(phone_number)
        
        # Check if state needs to be reset or initialized
        needs_reset = False
        
        if not state:
            print(f"No existing state found for {phone_number}")
            needs_reset = True
        else:
            # Check if the state has all required fields and matches current job
            required_fields = ['job_title', 'current_question', 'responses', 'step']
            for field in required_fields:
                if field not in state:
                    print(f"Missing required field '{field}' in state for {phone_number}")
                    needs_reset = True
                    break
            
            # Also check if job title changed
            if not needs_reset and state.get('job_title') != job_title:
                print(f"Job title changed from {state.get('job_title')} to {job_title} for {phone_number}")
                needs_reset = True
        
        if needs_reset:
            print(f"Initializing/resetting state for {phone_number}")
            initial_state = {
                'job_title': job_title,
                'current_question': 0,  # Start from the first question
                'responses': [],  # Initialize an empty list for responses
                'step': 'asking_questions',  # Track the current step
                'created_at': datetime.now(timezone.utc).isoformat()
            }
            # Initialize state in Redis
            state_manager.set_user_state(phone_number, initial_state)
        else:
            print(f"Using existing valid state for {phone_number}")
    
    def get_current_state(self):
        """Get current state from Redis with validation"""
        state = state_manager.get_user_state(self.phone_number)
        
        # If state is None or missing critical fields, reinitialize
        if not state or 'current_question' not in state or 'responses' not in state:
            print(f"State corruption detected for {self.phone_number}, reinitializing...")
            initial_state = {
                'job_title': self.job_title,
                'current_question': 0,
                'responses': [],
                'step': 'asking_questions',
                'created_at': datetime.now(timezone.utc).isoformat()
            }
            state_manager.set_user_state(self.phone_number, initial_state)
            return initial_state
        
        return state
    
    def get_question(self):
        """Get the current question"""
        state = self.get_current_state()
        current_q = state.get('current_question', 0)  # Default to 0 if missing
        
        if current_q < len(self.questions):
            base_question = self.questions[current_q]["question"]
            
            # Add some casual prefixes occasionally
            prefixes = [
                "", "Let me ask you - ", "I'd like to know - ",
                "Here's my next question: ", "Alright, "
            ]
            
            # 30% chance to add a prefix
            if random.random() < 0.3:
                prefix = random.choice(prefixes[1:])  # Exclude empty string
                return prefix + base_question.lower()
            
            return base_question
        else:
            return "Thank you for answering all the questions!"
    
    def add_response(self, response: str):
        """Add user response to state"""
        state = self.get_current_state()
        
        # Initialize 'responses' if not present (safety check)
        if 'responses' not in state:
            state['responses'] = []
        
        # Initialize 'current_question' if not present (safety check)
        if 'current_question' not in state:
            state['current_question'] = 0
        
        state['responses'].append(response)
        state['current_question'] += 1  # Move to the next question
        state_manager.set_user_state(self.phone_number, state)
        
        # Extend TTL for active conversations
        state_manager.extend_user_state_ttl(self.phone_number)
    
    def is_completed(self):
        """Check if all questions are answered"""
        state = self.get_current_state()
        current_question = state.get('current_question', 0)
        return current_question >= len(self.questions)
    
    def get_responses(self):
        """Get all responses"""
        state = self.get_current_state()
        return state.get('responses', [])

def reset_user_conversation(phone_number):
    """Helper function to reset a user's conversation state"""
    print(f"Resetting conversation for {phone_number}")
    state_manager.delete_user_state(phone_number)

@app.route('/sms', methods=['POST'])
def sms_reply():
    incoming_msg = request.form.get("Body")
    from_number = request.form.get("From")
    
    print(f"SMS from {from_number}: {incoming_msg}")
    
    # Return empty TwiML response immediately
    resp = MessagingResponse()
    
    # Check for reset command
    if incoming_msg and incoming_msg.strip().lower() in ['reset', 'start over', 'restart']:
        reset_user_conversation(from_number)
        reset_msg = "Great! Let's start fresh. What position are you applying for?"
        send_delayed_message.delay(reset_msg, from_number, 2)
        return str(resp)
    
    # Get current user state from Redis
    user_state = state_manager.get_user_state(from_number)
    
    if not user_state or user_state.get('step') not in ['ask_job_title', 'ask_email', 'asking_questions', 'completed']:
        # New user or corrupted state - ask for job title and initialize state
        state_manager.set_user_state(from_number, {
            'step': 'ask_job_title',
            'created_at': datetime.now(timezone.utc).isoformat()
        })
        
        welcome_message = add_human_touch_to_message("Thanks for responding!")
        send_delayed_message.delay(welcome_message, from_number, 2)
        
        job_request = "Before we get started, could you please specify the position you applied for?"
        send_delayed_message.delay(job_request, from_number, 5)
        
        return str(resp)
    
    # Handle job title input
    if user_state['step'] == 'ask_job_title':
        job_title = incoming_msg.strip()
        job_questions = load_job_questions()
        
        matched_job = None
        for job in job_questions.keys():
            if job_title.lower() in job.lower() or job.lower() in job_title.lower():
                matched_job = job
                break
        
        if matched_job:
            # Move to email collection step
            state_manager.update_user_state(from_number, {
                'step': 'ask_email',
                'job_title': matched_job
            })
            
            confirmation = f"Perfect! I see you're interested in the {matched_job} position."
            send_delayed_message.delay(confirmation, from_number, 2)
            
            email_request = "Great! To proceed with your application, could you please provide your email address?"
            send_delayed_message.delay(email_request, from_number, 4)
        else:
            available_jobs = ", ".join(job_questions.keys())
            error_msg = f"Hmm, I couldn't find that position. Available roles: {available_jobs}. Could you try again?"
            send_delayed_message.delay(error_msg, from_number, 3)
        
        return str(resp)
    
    # Handle email input
    if user_state['step'] == 'ask_email':
        email = incoming_msg.strip()
        
        # Basic email validation
        if '@' not in email or '.' not in email:
            error_msg = "That doesn't look like a valid email address. Could you please try again?"
            send_delayed_message.delay(error_msg, from_number, 2)
            return str(resp)
        
        # Search for person in Loxo
        person_info = search_person_by_email(email)
        
        if person_info:
            person_name, person_id, phone_number = person_info
            greeting_name = f"Hi {person_name}! " if person_name else "Hi there! "
        else:
            person_name = None
            person_id = None
            greeting_name = "Hi there! "
            print(f"Person not found in Loxo for email: {email}")
        
        # Update state with person information and initialize conversation state
        state_manager.update_user_state(from_number, {
            'step': 'asking_questions',
            'email': email,
            'person_id': person_id,
            'person_name': person_name,
            'current_question': 0,  # Initialize question tracking
            'responses': []  # Initialize responses list
        })
        
        # Send greeting and start questions
        greeting_msg = f"{greeting_name}Thanks for providing your email! Let me ask you a few questions to get to know you better."
        send_delayed_message.delay(greeting_msg, from_number, 3)
        
        # Initialize agent and send first question
        agent = JobScreeningAgent(user_state['job_title'], from_number)
        question = agent.get_question()
        send_delayed_message.delay(question, from_number, 5)
        
        return str(resp)
    
    # Handle completed conversations
    if user_state['step'] == 'completed':
        final_msg = "Thanks for your interest! Someone will be in touch soon. Have a great day!\n\nType 'reset' if you want to start a new conversation."
        send_delayed_message.delay(final_msg, from_number, 2)
        return str(resp)
    
    # Handle active conversation
    if user_state['step'] == 'asking_questions':
        try:
            agent = JobScreeningAgent(user_state['job_title'], from_number)
            agent.add_response(incoming_msg)
            
            print(f"Current question index: {agent.get_current_state()['current_question']}")
            print(f"Total questions: {len(agent.questions)}")
            print(f"Is completed: {agent.is_completed()}")
            
            # Send acknowledgment occasionally
            if random.random() < 0.4:
                acknowledgments = ["Got it!", "Thanks for that info.", "Interesting!", "I see.", "Noted."]
                ack = random.choice(acknowledgments)
                send_delayed_message.delay(ack, from_number, 1.5)
            
            if agent.is_completed():
                print(f"Conversation completed for {from_number}")
                
                # All questions answered
                completion_msg = add_human_touch_to_message("Thank you for answering all the questions!")
                send_delayed_message.delay(completion_msg, from_number, 4)
                
                followup_msg = "Someone from our team will review your responses and get back to you soon. Have a great day!"
                send_delayed_message.delay(followup_msg, from_number, 8)
                
                # Mark as completed
                state_manager.update_user_state(from_number, {'step': 'completed'})
                
                # Start async evaluation
                responses = agent.get_responses()
                person_id = user_state.get('person_id')
                
                print(f"Triggering evaluation for {from_number}")
                print(f"Person ID: {person_id}")
                print(f"Responses: {responses}")
                
                process_evaluation.delay(
                    from_number, 
                    responses, 
                    user_state['job_title'],
                    person_id
                )
                
            else:
                # Send next question
                question = agent.get_question()
                delay = random.uniform(4, 7)
                send_delayed_message.delay(question, from_number, delay)
                
        except Exception as e:
            print(f"Error in conversation handling: {e}")
            # Reset conversation on error
            reset_user_conversation(from_number)
            error_msg = "Sorry, something went wrong. Let's start fresh! What position are you applying for?"
            send_delayed_message.delay(error_msg, from_number, 3)
    
    return str(resp)

@app.route('/health')
def health_check():
    """Health check endpoint"""
    try:
        # Check Redis connection
        redis_client.ping()
        redis_status = "healthy"
    except:
        redis_status = "unhealthy"
    
    return jsonify({
        "status": "healthy",
        "service": "SMS Job Screening Bot",
        "redis": redis_status,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

@app.route('/stats')
def get_stats():
    """Get basic stats from Redis"""
    try:
        active_conversations = len(redis_client.keys("user_state:*"))
        
        return jsonify({
            "active_conversations": active_conversations,
            "redis_memory_usage": redis_client.info('memory')['used_memory_human'],
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/reset/<path:phone_number>')
def reset_user_endpoint(phone_number):
    """Endpoint to manually reset a user's conversation"""
    reset_user_conversation(phone_number)
    return jsonify({
        "message": f"Reset conversation for {phone_number}",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

@app.route('/test-celery')
def test_celery():
    """Test Celery task processing"""
    try:
        # Test a simple delayed message
        result = send_delayed_message.delay(
            "This is a test message from Celery", 
            "+1234567890",  # Dummy number
            1
        )
        return jsonify({
            "message": "Celery test task queued",
            "task_id": result.id,
            "status": "queued",
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        return jsonify({
            "error": f"Celery test failed: {str(e)}",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }), 500

@app.route('/test')
def test_endpoint():
    """Test endpoint to verify the bot is working"""
    return jsonify({
        "message": "SMS Job Screening Bot is running!",
        "endpoints": {
            "/sms": "Main SMS webhook endpoint",
            "/health": "Health check",
            "/stats": "Redis statistics",
            "/reset/<phone_number>": "Reset user conversation",
            "/test-celery": "Test Celery worker",
            "/test": "This test endpoint"
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

if __name__ == "__main__":
    print("🚀 WhatsApp Job Screening Bot is starting...")
    print("📱 Using Twilio WhatsApp Sandbox for testing")
    print("\nRequired environment variables:")
    print("   ✅ TWILIO_SID, TWILIO_AUTH_TOKEN")
    print("   ✅ OPENAI_API_KEY") 
    print("   📋 TWILIO_WHATSAPP_NUMBER (optional - defaults to sandbox)")
    print("   🔧 LOXO_API, LOXO_AGENCY_SLUG (optional for testing)")
    print("   🗄️  REDIS_URL (optional, defaults to redis://localhost:6379/0)")
    print("\n🔧 Make sure Redis server is running!")
    print("🏃‍♂️ Start Celery worker with:")
    print("   celery -A app.celery_app worker --loglevel=info --queues=whatsapp_queue,evaluation_queue,loxo_queue --concurrency=4")
    print("\n🌐 Webhook URL should be: https://your-domain.com/whatsapp")
    print("\n🔄 Users can type 'reset' to start over")
    
    app.run(port=5000, debug=False)