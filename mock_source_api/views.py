from django.http import JsonResponse

MOCK_POSTS = [
    {
        "source_type": "text",
        "organization_name": "APC-JPCS",
        "title": "Intro to Web Development Bootcamp",
        "content": "APC-JPCS is holding a beginner-friendly web development bootcamp on September 24, 2026 from 1:00 PM to 4:00 PM in Room 305. Bring your own laptop.",
        "posted_at": "2026-09-10T10:00:00Z",
    },
    {
        "source_type": "text",
        "organization_name": "BRIDGE",
        "title": "International Culture Exchange Night",
        "content": "BRIDGE presents a culture exchange night featuring food and traditions from around the world on September 26, 2026, 5:00 PM, at MPH1.",
        "posted_at": "2026-09-11T15:00:00Z",
    },
]

def mock_posts(request):
    return JsonResponse(MOCK_POSTS, safe=False)