import os
import random
import subprocess
import pickle
import ffmpeg
import openai

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# ===================== CONFIG =====================
CLIPS_PER_DAY = 5

SOURCE_CHANNELS = [
    "@TheDiaryOfACEO",
    "@ImpactTheory",
    "@MyFirstMillionPod",
]

MIN_CLIP_SECONDS = 15
MAX_CLIP_SECONDS = 60
UPLOAD_PRIVACY = "public"

PROCESSED_VIDEOS_FILE = "processed_videos.txt"
TOKEN_FILE = "token.pickle"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# ===================== API KEYS =====================
openai.api_key = os.environ.get("OPENAI_API_KEY")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

# ===================== UTILS =====================

def load_processed_videos():
    if not os.path.exists(PROCESSED_VIDEOS_FILE):
        return set()
    with open(PROCESSED_VIDEOS_FILE, "r") as f:
        return set(x.strip() for x in f.readlines())

def save_processed_video(video_id):
    with open(PROCESSED_VIDEOS_FILE, "a") as f:
        f.write(video_id + "\n")

def extract_video_id(url):
    return url.split("v=")[-1]

# ===================== OAUTH =====================

def get_authenticated_service():
    creds = None

    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "client_secret.json",
                SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "wb") as token:
            pickle.dump(creds, token)

    return build("youtube", "v3", credentials=creds)

# ===================== YOUTUBE =====================

def get_latest_videos(channels, max_results=3):
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    videos = []

    for ch in channels:
        if ch.startswith("@"):
            handle = ch[1:]
            resp = youtube.channels().list(
                part="id",
                forUsername=handle
            ).execute()

            if not resp.get("items"):
                print(f"❌ Could not resolve {ch}")
                continue

            channel_id = resp["items"][0]["id"]
        else:
            channel_id = ch

        search = youtube.search().list(
            part="snippet",
            channelId=channel_id,
            maxResults=max_results,
            order="date",
            type="video"
        ).execute()

        for item in search.get("items", []):
            video_id = item["id"]["videoId"]
            videos.append(f"https://www.youtube.com/watch?v={video_id}")

    return videos

# ===================== VIDEO =====================

def download_video(url):
    video_id = extract_video_id(url)
    filename = f"{video_id}.mp4"
    subprocess.run(
        ["yt-dlp", "-f", "best", url, "-o", filename],
        check=True
    )
    return filename

def transcribe_video(filename):
    try:
        import whisper
        model = whisper.load_model("base")
        result = model.transcribe(filename)
        return result.get("segments", [])
    except Exception as e:
        print(f"Whisper error: {e}")
        return []

def select_clips(segments):
    clips = []
    for s in segments:
        duration = s["end"] - s["start"]
        text = s.get("text", "").strip()
        if MIN_CLIP_SECONDS <= duration <= MAX_CLIP_SECONDS and text:
            clips.append({
                "start": s["start"],
                "end": s["end"],
                "text": text
            })
    return clips

def cut_clip(start, end, index, video_file):
    out_file = f"clip_{index}_{video_file}"
    (
        ffmpeg
        .input(video_file, ss=start, to=end)
        .output(out_file, vcodec="libx264", acodec="aac")
        .overwrite_output()
        .run(quiet=True)
    )
    return out_file

# ===================== AI =====================

def generate_title(text):
    prompt = f"Create a viral YouTube Shorts title (max 6 words): {text}"
    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=15,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Title error: {e}")
        return "Podcast Clip"

# ===================== UPLOAD =====================

def upload_to_youtube(filename, title):
    youtube = get_authenticated_service()

    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": "Auto-generated podcast clip",
                "tags": ["shorts", "podcast", "business"]
            },
            "status": {
                "privacyStatus": UPLOAD_PRIVACY
            }
        },
        media_body=MediaFileUpload(filename)
    )

    response = request.execute()
    print(f"✅ Uploaded: {title}")
    print(f"📺 Video ID: {response['id']}")
    os.remove(filename)

# ===================== MAIN =====================

def run():
    processed = load_processed_videos()
    urls = get_latest_videos(SOURCE_CHANNELS)

    uploaded = 0

    for url in urls:
        if uploaded >= CLIPS_PER_DAY:
            break

        video_id = extract_video_id(url)
        if video_id in processed:
            print(f"⏭ Skipping {video_id}")
            continue

        print(f"🎬 Processing {video_id}")

        try:
            video_file = download_video(url)
        except Exception as e:
            print(f"Download failed: {e}")
            continue

        segments = transcribe_video(video_file)
        clips = select_clips(segments)
        random.shuffle(clips)

        for i, clip in enumerate(clips):
            if uploaded >= CLIPS_PER_DAY:
                break

            try:
                clip_file = cut_clip(
                    clip["start"],
                    clip["end"],
                    i,
                    video_file
                )
                title = generate_title(clip["text"])
                upload_to_youtube(clip_file, title)
                uploaded += 1
            except Exception as e:
                print(f"Clip error: {e}")

        os.remove(video_file)
        save_processed_video(video_id)

    print("🎉 ALL DONE")

if __name__ == "__main__":
    run()
