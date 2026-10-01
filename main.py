import base64
import os
import logging
from functools import lru_cache

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict
from openai import OpenAI
import requests
from urllib.parse import urlparse, quote, unquote


logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 10 * 1024 * 1024
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@lru_cache
def get_client():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="AI service is not configured")
    return OpenAI(api_key=key, timeout=45.0, max_retries=0)


def generate_text(content):
    client = get_client()
    try:
        response = client.responses.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            input=[{"role": "user", "content": content}],
        )
        text = response.output_text.strip()
        if not text:
            raise ValueError("Empty model response")
        return text
    except Exception:
        logger.exception("AI request failed")
        raise HTTPException(status_code=502, detail="AI service could not complete the request") from None

app = FastAPI(
    title="TourGuideAI Vision Backend",
    description="Recognizes landmark photos and generates Wikipedia-grounded tour stories.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

WIKIPEDIA_HEADERS = {
    "User-Agent": "TourGuideAI/1.0 (https://github.com/Ivan-here/Tour-Guide-App-Backend)"
}

class LandmarkResponse(BaseModel):
    landmark_name: str
    raw_model_response: str


class StoryRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    landmark: str = Field(min_length=1, max_length=200)
    style: str = Field(default="neutral", min_length=1, max_length=80)
    tone: str = Field(default="casual", min_length=1, max_length=80)
    length: str = Field(default="medium", pattern="^(short|medium|long)$")


class StoryResponse(BaseModel):
    landmark: str
    summary: str
    story: str

WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_BASE_PAGE_URL = "https://en.wikipedia.org/wiki/"


def get_wikipedia_url(landmark: str) -> str | None:
    params = {
        "action": "query",
        "list": "search",
        "srsearch": landmark.strip(),
        "format": "json",
        "utf8": 1,
    }

    try:
        resp = requests.get(WIKIPEDIA_API_URL, headers=WIKIPEDIA_HEADERS, params=params, timeout=5)
        if resp.status_code != 200:
            print("Wiki search HTTP error:", resp.status_code)
            return None

        data = resp.json()
        search_results = data.get("query", {}).get("search", [])
        if not search_results:
            print("Wiki search: no results for", landmark)
            return None

        top_result = search_results[0]
        title = top_result.get("title")
        if not title:
            print("Wiki search: top result has no title")
            return None

        slug = quote(title.replace(" ", "_"), safe="")
        url = f"{WIKIPEDIA_BASE_PAGE_URL}{slug}"
        print(f"[Wikipedia] Resolved '{landmark}' -> {url}")
        return url

    except Exception as e:
        print("Wiki search exception:", repr(e))
        return None

@app.get("/health")
def health():
    return {"status": "ok", "ai_configured": bool(os.environ.get("OPENAI_API_KEY", "").strip())}


@app.post("/recognize-landmark", response_model=LandmarkResponse)
def recognize_landmark(image: UploadFile = File(...)):
    if image.content_type not in SUPPORTED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Use a JPEG, PNG, WebP, or GIF image")

    image_bytes = image.file.read(MAX_IMAGE_BYTES + 1)
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Image is empty")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 10 MiB limit")

    try:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{image.content_type};base64,{b64_image}"

        prompt = (
            "You are a landmark recognition service. "
            "Look at this image and identify the SINGLE most likely famous landmark, "
            "monument, building, or well-known place of interest. "
            "If you cannot confidently identify a specific landmark, respond exactly with: Unknown landmark.\n\n"
            "Respond with ONLY the name of the landmark or 'Unknown landmark'."
        )

        model_text = generate_text([
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": data_url},
        ])
        if model_text.lower().strip(" .\"'") in {"unknown", "unknown landmark", "not a landmark"}:
            landmark_name = "Unknown landmark"
        else:
            landmark_name = model_text.strip().strip('"').strip("'")

        return LandmarkResponse(
            landmark_name=landmark_name,
            raw_model_response=model_text,
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Vision processing failed")
        raise HTTPException(status_code=500, detail="Vision processing failed") from None

def get_wikipedia_summary(landmark: str) -> str | None:

    wiki_url = get_wikipedia_url(landmark)
    if not wiki_url:
        return None

    try:
        path = urlparse(wiki_url).path        # "/wiki/CN_Tower"
        slug = path.rsplit("/", 1)[-1]        # "CN_Tower"
        title = unquote(slug).replace("_", " ")

        params = {
            "action": "query",
            "prop": "extracts",
            "exintro": 1,         # only intro paragraph
            "explaintext": 1,     # plain text (no HTML)
            "titles": title,
            "redirects": 1,
            "format": "json",
            "utf8": 1,
        }

        resp = requests.get(WIKIPEDIA_API_URL, params=params, headers=WIKIPEDIA_HEADERS, timeout=5)
        if resp.status_code != 200:
            print("Wiki summary HTTP error:", resp.status_code)
            return None

        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        if not pages:
            print("Wiki summary: no pages in response")
            return None

        # pages is a dict keyed by pageid
        first_page = next(iter(pages.values()))
        extract = first_page.get("extract")
        if not extract:
            print("Wiki summary: 'extract' missing for", title)
            return None

        print(f"[Wikipedia] Summary found for '{landmark}' via title '{title}'")
        return extract

    except Exception as e:
        print("Wiki summary exception:", repr(e))
        return None

@app.post("/generate-story", response_model=StoryResponse)
def generate_story(payload: StoryRequest):
    """
    1. Takes a landmark + user preferences (style, tone, length).
    2. Fetches Wikipedia summary.
    3. Calls OpenAI LLM to generate a customized story.
    4. Returns summary + story.
    """

    wiki_summary = get_wikipedia_summary(payload.landmark)

    if not wiki_summary:
        raise HTTPException(
            status_code=404,
            detail=f"Wikipedia summary not found for landmark '{payload.landmark}'",
        )

    prompt = (
        "You are a storytelling assistant for a mobile tour guide app.\n\n"
        f"Landmark name: {payload.landmark}\n"
        f"User style preference: {payload.style}\n"
        f"User tone preference: {payload.tone}\n"
        f"Requested length: {payload.length}\n\n"
        "Use the following factual information from Wikipedia as your knowledge base:\n"
        "-----\n"
        f"{wiki_summary}\n"
        "-----\n\n"
        "Now write a narrative-style story about this landmark. "
        "Requirements:\n"
        "- Respect the style and tone preferences.\n"
        "- Avoid bullet points; write as a continuous story.\n"
        "- Do NOT mention Wikipedia or that this text came from an API.\n"
        "- Keep it readable on a phone (2–5 short paragraphs).\n"
    )

    story_text = generate_text([{"type": "input_text", "text": prompt}])

    return StoryResponse(
        landmark=payload.landmark,
        summary=wiki_summary,
        story=story_text,
    )
