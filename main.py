import os
import random
import subprocess
import openai
import ffmpeg
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

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
PROCESSED_VIDEOS_FILE = "processed_videos.txt"

# ---------------- API KEYS ----------------
openai.api_key = os.environ.get("OPENAI_API_KEY")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

# ---------------- UTILS ----------------

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

# ---------------- YOUTUBE ----------------

def get_latest_videos(channel_identifiers, max_results=3):
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    videos = []

    for identifier in channel_identifiers:
        # Handle → channelId
        if identifier.startswith("@"):
            handle = identifier[1:]
            resp = youtube.channels().list(
                part="id",
                forUsername=handle
            ).execute()
            items = resp.get("items", [])
            if not items:
                print(f"Could not resolve channel {identifier}")
                continue
            channel_id = items[0]["id"]
        else:
            channel_id = identifier

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

# ---------------- VIDEO ----------------

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

# ---------------- AI ----------------

def generate_title(text):
    prompt = f"Create a catchy YouTube Shorts title (max 6 words): {text}"
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

# ---------------- UPLOAD ----------------

def upload_to_youtube(filename, title):
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": "Auto-generated clip",
                "tags": ["shorts", "podcast", "business"]
            },
            "status": {"privacyStatus": UPLOAD_PRIVACY}
        },
        media_body=MediaFileUpload(filename)
    )
    request.execute()
    print(f"Uploaded: {title}")
    os.remove(filename)

# ---------------- MAIN ----------------

def run():
    processed_videos = load_processed_videos()
    video_urls = get_latest_videos(SOURCE_CHANNELS)

    clips_uploaded = 0

    for url in video_urls:
        if clips_uploaded >= CLIPS_PER_DAY:
            break

        video_id = extract_video_id(url)
        if video_id in processed_videos:
            print(f"Skipping processed video {video_id}")
            continue

        print(f"Processing {video_id}")

        try:
            video_file = download_video(url)
        except Exception as e:
            print(f"Download failed: {e}")
            continue

        segments = transcribe_video(video_file)
        clips = select_clips(segments)
        random.shuffle(clips)

        for i, clip in enumerate(clips):
            if clips_uploaded >= CLIPS_PER_DAY:
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
                clips_uploaded += 1
            except Exception as e:
                print(f"Clip error: {e}")

        os.remove(video_file)
        save_processed_video(video_id)

    print("Done.")

if __name__ == "__main__":
    run()
