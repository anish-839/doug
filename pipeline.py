from db import init_db, is_event_processed, mark_event_processed
from logging_setup import setup_logger
import time
from recording_auto import get_person_events, download_document, get_person_details, update_person_description, transcribe_and_summarize

logger = setup_logger()

def run_pipeline_summary(job_id, progress_callback=None, stop_requested=None):
    events = get_person_events(job_id)
    total_events = len(events)
    processed = skipped = 0

    for ev in events:
        if stop_requested and stop_requested():
            break

        if is_event_processed(ev["id"]):
            skipped += 1
        else:
            person_id = ev.get("person_id")
            documents = ev.get("documents", [])

            if not documents:
                skipped += 1
            else:
                for doc in documents:
                    doc_id = doc.get("id")
                    filename = f"{person_id}_{doc_id}.wav"
                    local_path = download_document(ev["id"], doc_id, filename)

                    if local_path:
                        summary = transcribe_and_summarize(local_path)
                        if summary:
                            old_desc = get_person_details(person_id)
                            updated_desc = (old_desc or "") + "\n\nInterview Summary:\n" + summary
                            update_person_description(person_id, updated_desc)

                mark_event_processed(ev["id"], job_id, person_id)
                processed += 1

        if progress_callback:
            progress_callback(total_events, processed + skipped)

        time.sleep(0.1)  # allow Streamlit to refresh UI

    return total_events, processed, skipped

