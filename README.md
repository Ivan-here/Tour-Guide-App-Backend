# TravelMate backend

This is the FastAPI server for [Tour-Guide-App](https://github.com/SaKsHaTGaRg/Tour-Guide-App). It identifies landmarks in photos using OpenAI, finds an introduction on Wikipedia, and turns that information into a story. The Android app handles the camera, narration, and saved history.

## Running locally

You'll need Python 3.11 or newer and an OpenAI API key.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Put your key in `.env` as `OPENAI_API_KEY`, then start the server:

```powershell
.\.venv\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 8000 --env-file .env
```

On macOS or Linux, use `.venv/bin/python` and `cp .env.example .env`. You can also set the environment variables directly; `main.py` doesn't read `.env` on its own.

Once it's running, open [the API docs](http://localhost:8000/docs) to try requests or [the health endpoint](http://localhost:8000/health) to check configuration. The server can start without a key, but AI requests will return 503 until you add one.

`OPENAI_MODEL` defaults to `gpt-4o-mini`. Change it in `.env` if needed, and restart after changing your configuration. OpenAI calls time out after 45 seconds without retrying; each Wikipedia request has a 5-second timeout.

## Using Docker

Create `.env` as above, then run:

```sh
docker compose up --build -d
docker compose logs -f vision-backend
```

To stop it, run `docker compose down`. Compose passes in `.env` at runtime; it isn't copied into the image.

## Endpoints

| Route | What to send | What comes back |
| --- | --- | --- |
| `GET /health` | Nothing | `status` and `ai_configured` |
| `POST /recognize-landmark` | A multipart file named `image` | `landmark_name` and `raw_model_response` |
| `POST /generate-story` | JSON with a landmark and optional preferences | `landmark`, `summary`, and `story` |

For example, a story request looks like this:

```json
{
  "landmark": "CN Tower",
  "style": "folklore",
  "tone": "casual",
  "length": "medium"
}
```

The landmark must be 1-200 characters after trimming whitespace. Style and tone accept 1-80 characters and default to `neutral` and `casual`. Length can be `short`, `medium`, or `long`; it defaults to `medium`.

Uploads accept JPEG, PNG, WebP, and GIF media types, up to 10 MiB. Empty files return 400, unsupported types return 415, and oversized files return 413. The server checks the media type rather than decoding the image itself. If recognition can't identify a place, it returns `Unknown landmark` with status 200.

Invalid request fields return 422. Story requests return 404 when a Wikipedia introduction isn't available, including when Wikipedia fails. OpenAI failures return 502 with a generic message; the details are logged on the server. The health endpoint checks whether a key is configured, not whether it works.

## Connecting the app

The Android emulator uses `http://10.0.2.2:8000` to reach this server. For a physical phone, use the same network and build the app with `-PBACKEND_BASE_URL=http://YOUR_LAN_IP:8000`. Your firewall needs to allow inbound traffic on port 8000. Release builds need HTTPS.

See the [Android README](https://github.com/SaKsHaTGaRg/Tour-Guide-App#readme) for the app setup.

## Tests

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
```

The tests cover validation, upload limits, missing keys, responses, unknown landmarks, provider errors, and Wikipedia titles with slashes or Unicode. They mock external services, so you don't need a key and won't make paid calls. They don't test real recognition accuracy.

Most of the server code is in `main.py`. Tests are in `tests/`, and the Docker files handle container startup.

## A few things to know

This server is set up for local development. It doesn't have authentication, rate limiting, or a database. Public hosting still needs access controls, HTTPS, and a request-size limit at the proxy, since the application checks upload size after multipart parsing. CORS allows all origins without credentials. The Wikipedia User-Agent links to this repo; use your own contact URL if you deploy it elsewhere.

Photos are sent to OpenAI. Landmark searches go to Wikipedia, and the retrieved summaries go to OpenAI for story generation. The results can be inaccurate. Saved stories live in the Android app, not here.
