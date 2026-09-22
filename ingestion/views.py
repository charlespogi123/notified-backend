import hmac
import json
import requests
import google.generativeai as genai
from django.conf import settings
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt
from pydantic import BaseModel, ValidationError
from typing import Optional

MOCK_API_URL = "http://127.0.0.1:8000/mock/posts/"

def fetch_from_source(request):
    # Step 1: pull raw posts from the mock "other team" API
    response = requests.get(MOCK_API_URL)
    posts = response.json()

    inserted = []
    errors = []

    for post in posts:
        payload = {
            "source_type": post.get("source_type", "text"),
            "source_reference": post.get("organization_name"),
            "raw_text": f"{post.get('title', '')}\n{post.get('content', '')}",
            "processing_status": "pending",
        }

        # Step 2: insert into Supabase's source_content table via its REST API
        supabase_response = requests.post(
            f"{settings.SUPABASE_URL}/rest/v1/source_content",
            headers={
                "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json=payload,
        )

        if supabase_response.status_code in (200, 201):
            inserted.append(supabase_response.json())
        else:
            errors.append({
                "post": post.get("title"),
                "status": supabase_response.status_code,
                "detail": supabase_response.text,
            })

    return JsonResponse({
        "inserted_count": len(inserted),
        "error_count": len(errors),
        "errors": errors,
    })


genai.configure(api_key=settings.GEMINI_API_KEY)

class InterpretedPost(BaseModel):
    type: str
    title: str
    description: Optional[str] = None
    organization_name: Optional[str] = None
    event_date: Optional[str] = None
    start_time: Optional[str] = None
    location: Optional[str] = None
    priority: Optional[str] = "medium"


PROMPT_TEMPLATE = """You are extracting structured data from a raw campus post.
Classify it as either "event" or "announcement", then extract fields.
Respond ONLY with valid JSON, no markdown, no explanation, matching this exact shape:
{{
  "type": "event" or "announcement",
  "title": "string",
  "description": "string or null",
  "organization_name": "string or null",
  "event_date": "YYYY-MM-DD or null",
  "start_time": "HH:MM or null",
  "location": "string or null",
  "priority": "low", "medium", or "high"
}}

Raw post:
{raw_text}
"""

def _interpret_row(row, model, headers):
    """Runs one source_content row through Gemini and writes the structured
    result to the event/announcement table. `headers` must not include Prefer."""
    prompt = PROMPT_TEMPLATE.format(raw_text=row["raw_text"])
    gemini_response = model.generate_content(prompt)

    try:
        cleaned = gemini_response.text.strip().strip('```json').strip('```').strip()
        parsed_json = json.loads(cleaned)
        structured = InterpretedPost(**parsed_json)
    except (ValidationError, json.JSONDecodeError) as e:
        requests.patch(
            f"{settings.SUPABASE_URL}/rest/v1/source_content?source_content_id=eq.{row['source_content_id']}",
            headers=headers,
            json={"processing_status": "failed"},
        )
        return {"source_content_id": row["source_content_id"], "error": str(e)}

    table = "event" if structured.type == "event" else "announcement"
    payload = {
        "source_content_id": row["source_content_id"],
        "organization_name": structured.organization_name or row.get("source_reference"),
        "title": structured.title,
        "priority": structured.priority,
    }
    if table == "event":
        payload["description"] = structured.description
        payload["event_date"] = structured.event_date
        payload["start_time"] = structured.start_time
        payload["location"] = structured.location
    else:
        payload["content"] = structured.description

    insert_response = requests.post(
        f"{settings.SUPABASE_URL}/rest/v1/{table}",
        headers={**headers, "Prefer": "return=representation"},
        json=payload,
    )

    requests.patch(
        f"{settings.SUPABASE_URL}/rest/v1/source_content?source_content_id=eq.{row['source_content_id']}",
        headers=headers,
        json={"processing_status": "processed"},
    )

    return {"source_content_id": row["source_content_id"], "table": table, "status": insert_response.status_code}


def interpret_pending_content(request):
    model = genai.GenerativeModel('gemini-3.6-flash')

    headers = {
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }

    pending_response = requests.get(
        f"{settings.SUPABASE_URL}/rest/v1/source_content?processing_status=eq.pending",
        headers=headers,
    )
    pending_rows = pending_response.json()

    results = [_interpret_row(row, model, headers) for row in pending_rows]

    return JsonResponse({"processed_count": len(results), "results": results})


@csrf_exempt
def event_approved_webhook(request):
    """Receives a Supabase Database Webhook call from the other team's project
    (table `events`, on UPDATE) and, when the update is an approval, ingests
    it into our source_content table and runs it through Gemini immediately.

    Configure in their Supabase dashboard under Database > Webhooks:
      table: events, events: Update, type: HTTP Request (POST),
      URL: https://<this-app>/ingestion/webhook/event-approved/
      header: X-Webhook-Secret: <EVENTS_WEBHOOK_SECRET value>
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    provided_secret = request.headers.get("X-Webhook-Secret", "")
    if not settings.EVENTS_WEBHOOK_SECRET or not hmac.compare_digest(provided_secret, settings.EVENTS_WEBHOOK_SECRET):
        return HttpResponseForbidden("invalid webhook secret")

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    record = payload.get("record") or {}
    old_record = payload.get("old_record") or {}

    if record.get("status") != "approved":
        return JsonResponse({"skipped": "not an approval"})
    if old_record.get("status") == "approved":
        return JsonResponse({"skipped": "already approved, no status change"})

    raw_text = "\n".join(filter(None, [
        record.get("title", ""),
        record.get("description", ""),
        record.get("organization") and f"Organization: {record['organization']}",
        record.get("venue") and f"Venue: {record['venue']}",
        record.get("event_date") and f"Date: {record['event_date']}",
        (record.get("start_time") or record.get("end_time"))
            and f"Time: {record.get('start_time', '')} - {record.get('end_time', '')}",
    ]))

    headers = {
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }

    insert_response = requests.post(
        f"{settings.SUPABASE_URL}/rest/v1/source_content",
        headers={**headers, "Prefer": "return=representation"},
        json={
            "source_type": "event_approval",
            "source_reference": record.get("organization") or record.get("id"),
            "raw_text": raw_text,
            "processing_status": "pending",
        },
    )

    if insert_response.status_code not in (200, 201):
        return JsonResponse(
            {"error": "failed to insert source_content", "detail": insert_response.text},
            status=502,
        )

    inserted_row = insert_response.json()[0]
    model = genai.GenerativeModel('gemini-3.6-flash')
    result = _interpret_row(inserted_row, model, headers)

    return JsonResponse({"ingested": True, "result": result})