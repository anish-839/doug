from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from langchain_community.llms import OpenAI
import os
from dotenv import load_dotenv
import json

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

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

# Store conversation state for each user
user_state = {}

class JobScreeningAgent:
    def __init__(self, job_title):
        self.job_title = job_title
        self.questions = job_questions.get(self.job_title, [])
        self.questions.extend(fixed_questions)  # Add fixed questions
        self.current_question = 0
        self.responses = []

    def get_question(self):
        # Return the current question
        if self.current_question < len(self.questions):
            return self.questions[self.current_question]["question"]
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
        """Modified to accept user input directly and skip interactive mode."""
        self.responses.append(user_input)  # Directly add the message to responses

        # Move to the next question
        if self.move_to_next_question():
            return "Thank you for answering all the questions!"

        # Get the next question
        question = self.get_question()
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

        # Print the raw evaluation output in the terminal
        print(f"Raw Evaluation Output: {evaluation}")  # Debug: Print raw output from LLM

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
                evaluation_data["overall_score"] = int(line.split(":")[1].strip())
            elif line.startswith("Qualifications score:"):
                evaluation_data["qualifications_score"] = int(line.split(":")[1].strip())
            elif line.startswith("Enthusiasm score:"):
                evaluation_data["enthusiasm_score"] = int(line.split(":")[1].strip())
            elif line.startswith("Availability score:"):
                evaluation_data["availability_score"] = int(line.split(":")[1].strip())
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

    # Check if user already has a conversation state
    if from_number not in user_state:
        # Start a new conversation if user doesn't exist in state
        agent = JobScreeningAgent("Shop Helper/Laborer")
        user_state[from_number] = {'agent': agent, 'question': 0}
        question = agent.get_question()
        user_state[from_number]['question'] = agent.current_question
        return send_message(question, from_number)

    # Get the existing agent and question
    agent = user_state[from_number]['agent']
    current_question = user_state[from_number]['question']

    # Run the agent with the incoming message
    chatbot_response = agent.run(incoming_msg)

    # Check if we have completed all questions
    if "Thank you" in chatbot_response:
        # Run evaluation once all questions are completed
        evaluation_result = agent.evaluate_with_llm(agent.responses)
        print(f"Evaluation Result: {evaluation_result}")  # Print evaluation result in the terminal
        
        # Reset the conversation state after evaluation
        del user_state[from_number]
        # Send final evaluation message
        #return send_message(evaluation_result['feedback'], from_number)

    # Otherwise, send the next question
    else:
        question = agent.get_question()
        user_state[from_number]['question'] = agent.current_question
        # Send only the next question (not the feedback)
        return send_message(chatbot_response, from_number, question)

    return send_message(chatbot_response, from_number)

def send_message(message, from_number, next_question=None):
    """Helper function to send WhatsApp messages."""
    resp = MessagingResponse()
    resp.message(message)  # Send the message (chatbot response or feedback)

    # Send the next question if available (only once)
    # if next_question:
    #     resp.message(next_question)

    return str(resp)





if __name__ == "__main__":
    app.run(port=5000, debug=True)
