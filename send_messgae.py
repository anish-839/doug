from twilio.rest import Client
import os
from dotenv import load_dotenv

load_dotenv()

# Your Twilio Sandbox credentials
ACCOUNT_SID = os.getenv("TWILIO_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

# Twilio Sandbox WhatsApp number (DO NOT change this)
FROM_NUMBER = 'whatsapp:+14155238886'  # Always this for sandbox

# The verified recipient number (must have joined sandbox using the code)
# TO_NUMBER = 'whatsapp:+'
TO_NUMBER = 'whatsapp:+919833944247'  # Replace with your verified WhatsApp number

client = Client(ACCOUNT_SID, AUTH_TOKEN)

message = client.messages.create(
    body="Your Application was Submitted!, Please be patient your resume is being processed atm, the team will get in touch with you. If you have any further questions please feel free to respond here",
    from_=FROM_NUMBER,
    to=TO_NUMBER
)

print(f"✅ WhatsApp message sent. SID: {message.sid}")
