import streamlit as st
from db import init_db
from pipeline import run_pipeline_summary

# Init DB
init_db()

st.title("🎙️ Loxo Transcription Dashboard")

# Session state
if "is_running" not in st.session_state:
    st.session_state.is_running = False
if "stop_requested" not in st.session_state:
    st.session_state.stop_requested = False

job_id = st.text_input("Enter Job ID")

def run_pipeline_ui(job_id):
    st.session_state.is_running = True
    st.session_state.stop_requested = False

    progress_placeholder = st.empty()
    progress_bar = st.progress(0)

    total, done, skipped = run_pipeline_summary(
        int(job_id),
        progress_callback=lambda total, completed: (
            progress_placeholder.markdown(
                f"**Processing...**\n\nTotal events: {total}\nCompleted: {completed}/{total}"
            ),
            progress_bar.progress(completed / total if total else 0)
        ),
        stop_requested=lambda: st.session_state.stop_requested
    )

    if not st.session_state.stop_requested:
        st.success(f"✅ Run complete for Job {job_id}")
        st.write(f"- Total events fetched: {total}")
        st.write(f"- Processed new events: {done}")
        st.write(f"- Skipped (already done): {skipped}")
    else:
        st.warning("⚠️ Run stopped by user")

    st.session_state.is_running = False

# Run button
if st.button("▶️ Run Pipeline", disabled=st.session_state.is_running):
    if job_id:
        run_pipeline_ui(job_id)
    else:
        st.warning("Please enter a Job ID.")

# Stop button
if st.session_state.is_running:
    if st.button("🛑 Stop Run", type="primary"):
        st.session_state.stop_requested = True
