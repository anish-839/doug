import streamlit as st
import openai
import PyPDF2
import os
import json
from dotenv import load_dotenv

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
def evaluate_candidate_with_llm(resume_text: str, job_description: str) -> str:
    """
    Unified GPT-4o prompt with:
    - Role auto-detect (Design Consultant / Installation Manager / CNC Operator)
    - Role-specific weighted scoring formulas
    - Hard caps by must-haves
    - Final band-mapping so scores match the recommendation buckets
    PASS -> 0–44, INTERVIEW -> 45–79, HIRE -> 80–100
    Returns: STRICT JSON only (use response_format={"type":"json_object"})
    """
    prompt = f"""
You are a senior recruiter. Be strict and realistic. Output ONLY valid JSON.

STEP 1 — Identify role_type from job description (choose one):
- "Design Consultant"
- "Installation Manager"
- "CNC Operator"

Heuristics:
- Showroom sales, luxury surfaces, design selections, high-end clientele → "Design Consultant"
- Managing flooring/countertop installations, crews, jobsites; often bilingual → "Installation Manager"
- CNC in stone countertop fabrication (Prussiani/Park/Breton/Baca, stone shop workflow) → "CNC Operator"

STEP 2 — Role rule-sets
A) Design Consultant (Luxury Surfaces / Showroom Sales) — STRICT
Must-haves:
• Showroom sales in luxury surfaces (stone/tile/countertops)
• High-end clientele consulting (designers/architects/builders)
• Design selections / builder packages
• Revenue ownership (quotas/targets)
Scoring caps:
• If no luxury showroom sales → cap overall_raw ≤ 55 (likely PASS/weak INTERVIEW)
• If no high-end clientele OR no selections/builder packages → cap overall_raw ≤ 65
Bonuses:
• Named luxury brands (Walker Zanger, Ann Sacks, Porcelanosa, etc.) +10 each (max +20)
Subscore emphasis:
• showroom_experience is critical
Weighted formula (Design Consultant):
overall_raw = 0.25*skills_match + 0.25*experience_match + 0.20*industry_alignment + 0.30*showroom_experience

B) Installation Manager (Flooring/Countertops) — BALANCED STRICT
Must-haves (ideal, but allow transferable):
• Direct oversight of flooring/countertop installs, crew/vendor scheduling, QC/safety, multi-site coordination
• Bilingual if JD requires (concern if missing, not auto-fail)
Strict-but-flexible:
• Strong transferable install/crew leadership without flooring/countertops → INTERVIEW possible (typically 65–75)
• Generic ops/warehouse w/o install oversight → PASS (<55)
Penalties:
• No install oversight −25; material mismatch (non-flooring/countertops) −20; missing bilingual (if required) −15
Subscore emphasis:
• install_oversight, material_experience
Weighted formula (Installation Manager):
overall_raw = 0.30*skills_match + 0.30*experience_match + 0.20*industry_alignment + 0.20*material_experience

C) CNC Operator (Stone Countertops) — MATERIAL-FOCUSED
Must-haves:
• CNC for stone countertops (granite/marble/quartz/quartzite/porcelain)
• Machines: Prussiani, Park Industries, Breton, Baca, Intermac (or comparable)
• Shop workflow: templating, cutting, polishing, slabs
Rules:
• Company names implying stone (e.g., "Stone", "Marble/Granite", "Countertops", "Stoneworks", “The Stone Resource”, “Mainland Stoneworks”, “Marble Granite World”, “CFI” if flooring context) → grant material credit
• Generic CNC (metal/wood/plastic) only → cap overall_raw ≤ 50 (PASS)
Subscore emphasis:
• stone_cnc_experience, material_experience
Weighted formula (CNC Operator):
overall_raw = 0.20*skills_match + 0.30*experience_match + 0.20*industry_alignment + 0.30*stone_cnc_experience

STEP 3 — Keyword aids (non-exhaustive)
STONE_SURFACES = ["stone","granite","marble","quartz","quartzite","countertop","countertops","slab","tile","porcelain","flooring","surfaces","fabrication","templating"]
STONE_CNC = ["Prussiani","Park Industries","Breton","Intermac","Baca","sawjet","bridge saw","waterjet","CNC sawjet"]
LUXURY_SHOWROOM = ["showroom","design center","selections","builder packages","high-end","luxury","designer clientele","spec homes"]
INDUSTRY_HINTS = ["Stone Resource","Mainland Stoneworks","Marble Granite World","Walker Zanger","Ann Sacks","Porcelanosa","CFI"]

STEP 4 — Global calibration
• Typical candidates: 45–70
• 65–85: strong INTERVIEW/HIRE potential with few gaps
• >85: exceptional direct fit
• If a role-critical must-have is missing, apply the role’s caps to overall_raw before recommendation

STEP 5 — Recommendation logic (decide BEFORE band-mapping)
• For Design Consultant: Missing luxury showroom → usually PASS; only INTERVIEW if adjacent elite retail design with clear high-end clientele & selections
• For Installation Manager: Allow INTERVIEW on strong transferable crew/field leadership even without flooring/countertops; PASS if no install oversight
• For CNC: PASS if no stone CNC; INTERVIEW/HIRE when stone CNC and/or stone shop machines/workflow are clear

STEP 6 — Band-mapping (make scores consistent with decision)
After caps and the weighted formula:
IF recommendation == "PASS": set overall_score in 0–44
IF recommendation == "INTERVIEW": set overall_score in 45–79
IF recommendation == "HIRE": set overall_score in 80–100
(Choose a value that reflects strength within the band. Never output a score outside its band.)

STEP 7 — Output STRICT JSON ONLY:
{{
"role_type": "Design Consultant" | "Installation Manager" | "CNC Operator",
"overall_score": , # AFTER band-mapping
"recommendation": "HIRE" | "INTERVIEW" | "PASS",
"skills_match": ,
"experience_match": ,
"industry_alignment": ,
"material_experience": ,
"showroom_experience": , # Design Consultant only; else null
"install_oversight": , # Installation Manager only; else null
"bilingual_fit": , # Installation Manager: 100 if not required; else score. Others: null
"stone_cnc_experience": , # CNC only; else null
"must_have_gaps": ["gap1","gap2"],
"strengths": ["s1","s2","s3"],
"concerns": ["c1","c2"],
"summary": "<2-3 concise recruiter-style sentences explaining the decision>"
}}

Now evaluate strictly but fairly.

JOB_DESCRIPTION:
{job_description}
RESUME:
{resume_text}
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o",  # Model name for chat-based models
            messages=[
                {"role": "system", "content": "You are a meticulous, conservative recruiter with 10+ years of experience in candidate evaluation."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=1500
        )
        message_content = response.choices[0].message.content.strip()
        print(f"📊 Raw LLM Response:\n{message_content}")

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
