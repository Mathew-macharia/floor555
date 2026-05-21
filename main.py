"""
Floor555 — personal YouTube + Spotify downloader
FastAPI backend with SSE progress streaming
"""

import asyncio
import json
import os
import re
import signal
import tempfile
import uuid
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

# ── Constants ────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
DOWNLOAD_ROOT = BASE_DIR / "downloads"
DOWNLOAD_ROOT.mkdir(exist_ok=True)

SUBFOLDER_RE = re.compile(r'^[a-zA-Z0-9_-]{1,40}$')

YOUTUBE_FORMATS = {
    "best-mp4":  ["--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                  "--merge-output-format", "mp4"],
    "720p":      ["--format", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
                  "--merge-output-format", "mp4"],
    "480p":      ["--format", "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]",
                  "--merge-output-format", "mp4"],
    "audio-mp3": ["--format", "bestaudio", "--extract-audio",
                  "--audio-format", "mp3", "--audio-quality", "0"],
    "audio-m4a": ["--format", "bestaudio[ext=m4a]/bestaudio",
                  "--extract-audio", "--audio-format", "m4a"],
}

ALLOWED_URL_PREFIXES = (
    "https://www.youtube.com/",
    "https://youtube.com/",
    "https://youtu.be/",
    "https://music.youtube.com/",
    "https://open.spotify.com/",
)

# ── In-memory job store ───────────────────────────────────────────────────────

# job_id -> {"process": asyncio.subprocess, "log": [str], "done": bool, "returncode": int|None}
JOBS: dict[str, dict] = {}

# ── App setup ────────────────────────────────────────────────────────────────

app = FastAPI(title="Floor555", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "connect-src 'self';"
    )
    return response


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# ── Helpers ───────────────────────────────────────────────────────────────────


def validate_subfolder(subfolder: str) -> Path:
    if not SUBFOLDER_RE.match(subfolder):
        raise HTTPException(status_code=400,
                            detail="Subfolder must be 1-40 characters: letters, digits, hyphens, underscores only.")
    target = (DOWNLOAD_ROOT / subfolder).resolve()
    if not str(target).startswith(str(DOWNLOAD_ROOT.resolve())):
        raise HTTPException(status_code=400, detail="Invalid subfolder path.")
    target.mkdir(parents=True, exist_ok=True)
    return target


def new_job() -> str:
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"process": None, "log": [], "done": False, "returncode": None}
    return job_id


def log(job_id: str, line: str):
    if job_id in JOBS:
        JOBS[job_id]["log"].append(line)


async def run_subprocess(job_id: str, cmd: list[str], cwd: Path):
    """Run a subprocess, stream its stdout/stderr into the job log."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd),
        preexec_fn=os.setsid,
    )
    JOBS[job_id]["process"] = proc

    async def read_stream():
        assert proc.stdout
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            if line:
                log(job_id, line)

    await read_stream()
    await proc.wait()
    JOBS[job_id]["returncode"] = proc.returncode
    JOBS[job_id]["done"] = True
    status = "done" if proc.returncode == 0 else f"error (exit {proc.returncode})"
    log(job_id, f"[system] Process finished — {status}")


# ── Schemas ───────────────────────────────────────────────────────────────────


class YoutubeInfoRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def check_url(cls, v):
        if not any(v.startswith(p) for p in ALLOWED_URL_PREFIXES[:4]):
            raise ValueError("Not a recognised YouTube URL")
        return v


class YoutubeDownloadRequest(BaseModel):
    url: str
    format: str
    # selected_indices: 1-based playlist item numbers to download; empty = all
    selected_indices: list[int] = []

    @field_validator("url")
    @classmethod
    def check_url(cls, v):
        if not any(v.startswith(p) for p in ALLOWED_URL_PREFIXES[:4]):
            raise ValueError("Not a recognised YouTube URL")
        return v

    @field_validator("format")
    @classmethod
    def check_format(cls, v):
        if v not in YOUTUBE_FORMATS:
            raise ValueError(f"Unknown format. Choose from: {list(YOUTUBE_FORMATS)}")
        return v


class SpotifyInfoRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def check_url(cls, v):
        if not v.startswith("https://open.spotify.com/"):
            raise ValueError("Not a recognised Spotify URL")
        return v


class SpotifyDownloadRequest(BaseModel):
    # urls: list of individual track URLs (selected subset) OR a single playlist/album URL
    urls: list[str]

    @field_validator("urls")
    @classmethod
    def check_urls(cls, v):
        if not v:
            raise ValueError("At least one URL required")
        for url in v:
            if not url.startswith("https://open.spotify.com/"):
                raise ValueError(f"Not a recognised Spotify URL: {url}")
        return v


# ── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/youtube/info")
async def youtube_info(req: YoutubeInfoRequest):
    """Fetch playlist/video titles without downloading."""
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        req.url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="yt-dlp info timed out")

    entries = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            entries.append({
                "title": data.get("title") or data.get("id", "Unknown"),
                "duration": data.get("duration"),
                "id": data.get("id"),
            })
        except json.JSONDecodeError:
            pass

    if not entries:
        raise HTTPException(status_code=422, detail="No video info returned. Check the URL.")

    return {"entries": entries}


@app.post("/api/youtube/download")
async def youtube_download(req: YoutubeDownloadRequest):
    target = validate_subfolder("youtube")
    format_args = YOUTUBE_FORMATS[req.format]

    job_id = new_job()
    log(job_id, f"[system] Starting YouTube download — format: {req.format}")
    log(job_id, f"[system] Output folder: downloads/youtube")

    cmd = ["yt-dlp", *format_args, "--newline", "--progress"]

    # Playlist handling
    if "list=" in req.url:
        if req.selected_indices:
            items_str = ",".join(str(i) for i in sorted(req.selected_indices))
            cmd += ["--playlist-items", items_str]
            log(job_id, f"[system] Downloading {len(req.selected_indices)} selected item(s)")
        else:
            cmd += ["--yes-playlist"]
    else:
        cmd += ["--no-playlist"]

    cmd += ["-o", "%(title)s.%(ext)s", req.url]

    asyncio.create_task(run_subprocess(job_id, cmd, target))
    return {"job_id": job_id}


@app.post("/api/spotify/info")
async def spotify_info(req: SpotifyInfoRequest):
    """Fetch Spotify playlist/album/track list without downloading."""
    tmp_file = Path(tempfile.mktemp(suffix=".spotdl"))
    cmd = ["spotdl", "save", req.url, "--save-file", str(tmp_file)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="spotdl info timed out")
    finally:
        pass

    entries = []
    if tmp_file.exists():
        try:
            with open(tmp_file) as f:
                data = json.load(f)
            # spotdl save format is a list of song dicts
            for song in data:
                artists = song.get("artists", [])
                artist_str = ", ".join(artists) if artists else ""
                entries.append({
                    "title": song.get("name", "Unknown"),
                    "artist": artist_str,
                    "url": song.get("url", ""),
                    "duration": song.get("duration", None),
                })
        except Exception:
            pass
        finally:
            tmp_file.unlink(missing_ok=True)

    if not entries:
        # Fallback: single track — just return the URL itself as one entry
        # Parse title from stdout if available
        out_text = stdout.decode("utf-8", errors="replace") if stdout else ""
        if "Found" in out_text or not out_text.strip():
            raise HTTPException(status_code=422,
                                detail="Could not fetch track list. Check Spotify credentials and URL.")
        raise HTTPException(status_code=422, detail="No tracks found. Check the URL.")

    return {"entries": entries}


@app.post("/api/spotify/download")
async def spotify_download(req: SpotifyDownloadRequest):
    target = validate_subfolder("spotify")

    job_id = new_job()
    log(job_id, "[system] Starting Spotify download via spotdl")
    log(job_id, f"[system] Downloading {len(req.urls)} track(s)")
    log(job_id, f"[system] Output folder: downloads/spotify")

    cmd = [
        "spotdl",
        "--output", "{artists} - {title}.{output-ext}",
        "--format", "mp3",
        "--bitrate", "320k",
        *req.urls,
    ]

    asyncio.create_task(run_subprocess(job_id, cmd, target))
    return {"job_id": job_id}


@app.get("/api/job/{job_id}/stream")
async def stream_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator() -> AsyncGenerator[str, None]:
        sent = 0
        while True:
            job = JOBS.get(job_id)
            if not job:
                break
            lines = job["log"]
            while sent < len(lines):
                payload = json.dumps({"line": lines[sent]})
                yield f"data: {payload}\n\n"
                sent += 1
            if job["done"]:
                rc = job.get("returncode")
                yield f"data: {json.dumps({'done': True, 'returncode': rc})}\n\n"
                break
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.delete("/api/job/{job_id}")
async def cancel_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    proc = job.get("process")
    if proc and proc.returncode is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            log(job_id, "[system] Cancelled by user")
        except ProcessLookupError:
            pass
    job["done"] = True
    return {"cancelled": True}


@app.get("/")
async def root():
    from fastapi.responses import FileResponse
    return FileResponse(BASE_DIR / "static" / "index.html")
