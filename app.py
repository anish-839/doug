import streamlit as st
import openai
import PyPDF2
import os
import json
from dotenv import load_dotenv
from typing import Dict, Any
# Load environment variables
load_dotenv()

# Set up OpenAI API key with client initialization (for openai>=1.0.0)
api_key = os.getenv("OPENAI_API_KEY")
client = openai.OpenAI(api_key=api_key)  # Initialize OpenAI client

# Function to extract text from a PDF file
def extract_text_from_pdf(pdf_file):
    pdf_reader = PyPDF2.PdfReader(pdf_file)
    text = ""
    for page_num in range(len(pdf_reader.pages)):
        page = pdf_reader.pages[page_num]
        text += page.extract_text()
    return text

# Function to send text to OpenAI API and get score and summary
def evaluate_candidate_with_llm(resume_text: str, job_description: str) -> Dict[str, Any]:
    """
    Properly calibrated evaluation: Catches major mismatches but rewards good candidates
    - Telecommunications/utilities → construction roles = major mismatch
    - Adjacent construction → construction roles = good match
    """
    
    prompt = f"""
You are a senior recruiter. Be precise about industry alignment - some industries truly don't transfer well to construction/installation roles.

OUTPUT REQUIREMENTS: Return ONLY valid JSON.

SCORING SYSTEM:
- 10-45: PASS (Major industry mismatch or missing critical requirements)
- 60-75: INTERVIEW (Relevant experience with some gaps)  
- 75+: HIRE (Strong direct or adjacent experience)

STEP 1: ROLE IDENTIFICATION
- "Design Consultant" - Luxury showroom sales, design selections
- "Installation Manager" - Flooring/countertop installation oversight
- "CNC Operator" - Stone fabrication, CNC operation
- "Other" - Any other role type

STEP 2: INDUSTRY MISMATCH ANALYSIS
Identify MAJOR INDUSTRY MISMATCHES that don't transfer to construction/installation:

HIGH-RISK MISMATCHES (Score 35-45 max):
• Telecommunications/Cable/Fiber (Conterra, Sparklight, Verizon, AT&T, Cox)
• Oil & Gas/Energy (ExxonMobil, Shell, Energy Transfer)
• Healthcare (hospitals, clinics, medical offices)
• Finance/Banking (Chase, Wells Fargo, investment firms)
• Pure Technology/Software (Google, Microsoft, pure IT roles)
• Food Service/Hospitality (restaurants, hotels)
• Retail (non-construction related like clothing, electronics)

ACCEPTABLE TRANSFERS (Score 60+ possible):
• General Construction Companies
• Home Improvement (Home Depot, Lowe's)
• Building Materials/Supply
• Trades (plumbing, electrical, HVAC)
• Manufacturing/Fabrication
• Warehousing/Logistics (with construction materials)
• Cabinet/Millwork shops
• Tile/Hardwood specialty companies

STEP 3: ROLE-SPECIFIC EVALUATION

A) DESIGN CONSULTANT
Requirements:
• Sales experience with premium/luxury products
• Client consultation skills
• Design/selection process knowledge

Scoring:
• Luxury surface sales → 80-90 (HIRE)
• Adjacent luxury sales (appliances, cabinets, high-end retail) → 70-75 (INTERVIEW/HIRE)
• General sales with consultative approach → 65-70 (INTERVIEW)
• Basic retail sales → 50-60 (PASS/weak INTERVIEW)
• No sales experience → 35 (PASS)

B) INSTALLATION MANAGER - BE SPECIFIC ABOUT MISMATCHES
Requirements:
• Installation/construction project oversight
• Crew/contractor management
• Quality control in physical construction
• Understanding of installation processes

CRITICAL MISMATCH RULE:
If candidate's background is primarily telecommunications, utilities, oil & gas, healthcare, finance, or pure office work → AUTOMATIC 45 or below (PASS)

Scoring for Construction-Adjacent Candidates:
• Direct flooring/countertop management → 80-90 (HIRE)
• General construction project management → 75-80 (HIRE)
• Cabinet/millwork installation management → 70-75 (INTERVIEW/HIRE)
• Other trades supervision (plumbing, electrical) → 65-70 (INTERVIEW)
• Manufacturing supervision with quality control → 60-65 (INTERVIEW)

C) CNC OPERATOR
Requirements:
• CNC machine operation
• Manufacturing/production environment
• Precision work and quality control

Scoring:
• Stone/countertop CNC → 85-95 (HIRE)
• Other material CNC (metal, wood) → 70-80 (INTERVIEW/HIRE)
• General manufacturing → 60-65 (INTERVIEW)
• No manufacturing experience → 40 (PASS)

STEP 4: SPECIFIC PENALTIES FOR MAJOR MISMATCHES

Apply these penalties BEFORE final scoring:

TELECOMMUNICATIONS/UTILITIES PENALTY: -25 points
• Companies: Conterra, Sparklight, Verizon, AT&T, Cox, Energy Transfer
• Reason: Infrastructure work ≠ residential installation work

OIL & GAS PENALTY: -20 points  
• Reason: Industrial processes ≠ home construction

FINANCE/HEALTHCARE/PURE TECH PENALTY: -30 points
• Reason: Office/service work ≠ physical construction

STEP 5: SCORING FACTORS
1. **Industry Match** (40%): How well the industry transfers to target role
2. **Role Experience** (30%): Direct experience in similar role functions  
3. **Skills Alignment** (20%): Technical and management capabilities
4. **Additional Factors** (10%): Bilingual, local market, progression

STEP 6: FINAL SCORE CALCULATION
Base Score = Weighted average of factors
Final Score = Base Score - Industry Mismatch Penalties
Cap scores based on industry mismatch severity

JSON OUTPUT:
{{
    "role_type": "Design Consultant|Installation Manager|CNC Operator|Other",
    "overall_score": <10-100>,
    "recommendation": "PASS|INTERVIEW|HIRE",
    "industry_match_level": "Direct|Adjacent|Transferable|Major Mismatch",
    "mismatch_penalty_applied": <0-30>,
    "primary_industries": ["industry1", "industry2"],
    "industry_match_score": <0-100>,
    "role_experience_score": <0-100>,
    "skills_alignment_score": <0-100>,
    "additional_factors_score": <0-100>,
    "critical_gaps": ["gap1", "gap2"],
    "strengths": ["strength1", "strength2"],
    "concerns": ["concern1", "concern2"],
    "years_relevant_experience": <number>,
    "summary": "Clear explanation of why score reflects industry transferability"
}}

EXAMPLES FOR CALIBRATION:

BAD MATCH - Should Score 35-45:
• Telecommunications technician → Installation Manager
• Bank manager → Design Consultant  
• Software developer → CNC Operator

GOOD MATCH - Should Score 70+:
• Cabinet installer → Flooring Installation Manager
• Appliance salesperson → Design Consultant
• Metal fabricator → Stone CNC Operator

EVALUATION RULES:
1. If primary background is telecommunications, utilities, finance, healthcare → Apply major mismatch penalty
2. If background is construction-adjacent (trades, manufacturing, building materials) → Allow higher scores
3. Consider skill transferability BUT industry context matters significantly
4. Don't penalize truly qualified candidates, but catch major mismatches

Evaluate the candidate:

JOB_DESCRIPTION:
{job_description}

RESUME:
{resume_text}
"""
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": """You are a construction industry recruiter who understands that some industries simply don't transfer well to installation/construction work.

KEY INSIGHTS:
- Telecommunications/utilities work is VERY different from flooring installation
- Office work (finance, healthcare, tech) doesn't transfer to hands-on construction
- But adjacent trades (cabinets, general construction, manufacturing) transfer well
- Be specific about industry context, not just job titles

IMPORTANT: A telecommunications technician managing fiber crews is NOT the same as managing flooring installation crews - different materials, different quality standards, different customer interactions.

Be fair to good candidates but catch major industry mismatches.

Return ONLY valid JSON."""
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.1,
            max_tokens=1500,
            response_format={"type": "json_object"}
        )
        
        response_text = response.choices[0].message.content.strip()
        print(f"📊 Raw LLM Response:\n{response_text}")

        if response_text.startswith('```json'):
            response_text = response_text[7:-3]
        elif response_text.startswith('```'):
            response_text = response_text[3:-3]
        
        result = json.loads(response_text)
        print(result)
        # Enforce mismatch penalties
        industry_match = result.get('industry_match_level', 'Transferable')
        score = result.get('overall_score', 50)
        
        if industry_match == "Major Mismatch" and score > 45:
            result['overall_score'] = 40
            result['recommendation'] = 'PASS'
        
        # Ensure score-recommendation alignment
        score = result['overall_score']
        if score <= 45:
            result['recommendation'] = 'PASS'
        elif score <= 75:
            result['recommendation'] = 'INTERVIEW'  
        else:
            result['recommendation'] = 'HIRE'
        
        return result
        
    except Exception as e:
        return {
            "role_type": "Unknown",
            "overall_score": 40,
            "recommendation": "PASS",
            "error": f"Error: {str(e)}",
            "summary": "Evaluation failed, defaulting to PASS for safety"
        }

# Streamlit app interface
st.title("Resume Evaluation App")

st.sidebar.header("Upload Files")
uploaded_pdf = st.sidebar.file_uploader("Upload your Resume (PDF)", type="pdf")
uploaded_txt = st.sidebar.file_uploader("Upload the Job Description (TXT)", type="txt")

if uploaded_pdf and uploaded_txt:
    # Extract resume text from PDF
    resume_text = extract_text_from_pdf(uploaded_pdf)

    # Read the job description text
    job_description_text = uploaded_txt.getvalue().decode("utf-8")

    # Only show the result after "Evaluate" button is pressed
    if st.button("Evaluate"):
        evaluation_result = evaluate_candidate_with_llm(resume_text, job_description_text)

        # Extract score and summary from the result
        score = evaluation_result.get('overall_score', 'N/A')
        summary = evaluation_result.get('summary', 'No summary available.')

        # Display the result
        st.subheader("Evaluation Result")
        st.write(f"Match Score: {score}/100")
        st.write("Summary:")
        st.write(summary)

else:
    st.info("Please upload both the resume and job description.")
