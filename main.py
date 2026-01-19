import os
import random
import subprocess
import openai
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import ffmpeg
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------- CONFIG ----------------
CLIPS_PER_DAY = 8
SOURCE_CHANNELS = [
    "@TheDiaryOfACEO",
    "@ImpactTheory",
    "@MyFirstMillionPod",
]
MIN_CLIP_SECONDS = 20
MAX_CLIP_SECONDS = 45
UPLOAD_PRIVACY = "public"
MAX_WORKERS = 3           # Number of videos to process concurrently
MAX_TITLE_WORKERS = 3     # Number of titles to generate concurrently
PROCESSED_VIDEOS_FILE = "processed_videos.txt"  # Tracks already processed videos

# ---------------- API KEYS ----------------
openai.api_key = os.environ.get("OPENAI_API_KEY")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

# ---------------- UTILITY FUNCTIONS ----------------

def load_processed_videos():
    if not os.path.exists(PROCESSED_VIDEOS_FILE):
        return set()
    with open(PROCESSED_VIDEOS_FILE, "r") as f:
        return set(line.strip() for line in f.readlines())

def save_processed_video(video_id):
    with open(PROCESSED_VIDEOS_FILE, "a") as f:
        f.write(video_id + "\n")

def extract_video_id(url):
    return url.split("v=")[-1]

# ---------------- YOUTUBE FUNCTIONS ----------------

def get_latest_videos(channel_identifiers, max_results=3):
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    videos = []

    for identifier in channel_identifiers:
        if identifier.startswith("@"):
            handle = identifier.lstrip("@")
            try:
                resp = youtube.channels().list(part="id", forUsername=handle).execute()
                items = resp.get("items", [])
                if not items:
                    print(f"Could not find channel ID for handle {identifier}, skipping.")
                    continue
                channel_id = items[0]["id"]
            except Exception as e:
                print(f"Error fetching channel ID for {identifier}: {e}")
                continue
        else:
            channel_id = identifier

        try:
            request = youtube.search().list(
                part="snippet",
                channelId=channel_id,
                maxResults=max_results,
                order="date",
                type="video"
            )
            response = request.execute()
            for item in response.get("items", []):
                video_id = item["id"]["videoId"]
                videos.append(f"https://www.youtube.com/watch?v={video_id}")
        except Exception as e:
            print(f"Error fetching videos for {identifier}: {e}")
            continue

    return videos

# ---------------- VIDEO PROCESSING FUNCTIONS ----------------

def download_video(url):
    filename = f"{url.split('=')[-1]}.mp4"
    cmd = ["yt-dlp", "-f", "best", url, "-o", filename]
    subprocess.run(cmd, check=True)
    return filename

def transcribe_video(filename):
    try:
        import whisper
        model = whisper.load_model("base")
        result = model.transcribe(filename)
        return result.get('segments', [])
    except Exception as e:
        print(f"Whisper not installed properly: {e}")
        return []

def select_clips(segments):
    clips = []
    for s in segments:
        start, end = s['start'], s['end']
        duration = end - start
        text = s.get('text', '').strip()
        if MIN_CLIP_SECONDS <= duration <= MAX_CLIP_SECONDS and text:
            clips.append({"start": start, "end": end, "text": text})
    return clips

def cut_clip_ffmpeg(start, end, index, video_file):
    out_file = f"clip_{index}_{video_file}"
    (
        ffmpeg
        .input(video_file, ss=start, to=end)
        .output(out_file, codec="libx264", acodec="aac", strict='experimental')
        .overwrite_output()
        .run(quiet=True)
    )
    return out_file

def generate_title(text):
    if not text.strip():
        return "Untitled Clip"
    prompt = f"Create a catchy YouTube Shorts title from this text (max 6 words): {text}"
    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=15
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error generating title: {e}")
        return "Untitled Clip"

def upload_to_youtube(filename, title):
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    body = {
        "snippet": {
            "title": title,
            "description": "Auto-generated clip",
            "tags": ["podcast", "business", "money", "shorts"],
        },
        "status": {"privacyStatus": UPLOAD_PRIVACY}
    }
    media = MediaFileUpload(filename)
    try:
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        request.execute()
        print(f"Uploaded {filename} with title: {title}")
        os.remove(filename)
        print(f"Deleted local clip file: {filename}")
    except Exception as e:
        print(f"Error uploading {filename}: {e}")

# ---------------- PROCESSING LOGIC ----------------

def process_clip(c, index, video_file):
    clip_file = cut_clip_ffmpeg(c["start"], c["end"], index, video_file)
    return clip_file, c["text"]

def process_video(url, clips_per_day, clips_uploaded_global, processed_videos):
    video_id = extract_video_id(url)
    if video_id in processed_videos:
        print(f"Skipping already processed video {video_id}")
        return clips_uploaded_global

    try:
        video_file = download_video(url)
    except Exception as e:
        print(f"Failed to download {url}: {e}")
        return clips_uploaded_global

    segments = transcribe_video(video_file)
    clips = select_clips(segments)
    random.shuffle(clips)

    clips_to_upload = clips[:max(0, clips_per_day - clips_uploaded_global)]
    clip_results = []

    with ThreadPoolExecutor(max_workers=MAX_
