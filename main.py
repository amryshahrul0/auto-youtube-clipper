import os
import random
import subprocess
import openai
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import ffmpeg  # ffmpeg-python

# ---------------- CONFIG ----------------
CLIPS_PER_DAY = 8
SOURCE_CHANNELS = [
    "https://www.youtube.com/@TheDiaryOfACEO",
    "https://www.youtube.com/@ImpactTheory",
    "https://www.youtube.com/@MyFirstMillionPod",
]
MIN_CLIP_SECONDS = 20
MAX_CLIP_SECONDS = 45
UPLOAD_PRIVACY = "public"

# ---------------- API KEYS ----------------
openai.api_key = os.environ.get("OPENAI_API_KEY")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

# ---------------- FUNCTIONS ----------------

def get_latest_videos(channel_urls, max_results=3):
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    videos = []

    for url in channel_urls:
        channel_id = url.split("/")[-1]
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
    return videos


def download_video(url):
    filename = "video.mp4"
    cmd = ["yt-dlp", "-f", "best", url, "-o", filename]
    subprocess.run(cmd, check=True)
    return filename


def transcribe_video(filename="video.mp4"):
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
        if MIN_CLIP_SECONDS <= duration <= MAX_CLIP_SECONDS:
            text = s.get('text', '').strip()
            if text:
                clips.append({"start": start, "end": end, "text": text})
    return clips


def cut_clip_ffmpeg(start, end, index):
    out_file = f"clip_{index}.mp4"
    (
        ffmpeg
        .input("video.mp4", ss=start, to=end)
        .output(out_file, codec="libx264", acodec="aac", strict='experimental')
        .overwrite_output()
        .run(quiet=True)
    )
    return out_file


def generate_title(text):
    """
    Generate a catchy YouTube Shorts title using OpenAI Chat API
    """
    if not text.strip():
        return "Untitled Clip"

    prompt = f"Create a catchy YouTube Shorts title from this text (max 6 words): {text}"

    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=15
    )
    title = response.choices[0].message.content.strip()
    return title


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
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    request.execute()
    print(f"Uploaded {filename} with title: {title}")


# ---------------- MAIN SCRIPT ----------------

def run():
    video_urls = get_latest_videos(SOURCE_CHANNELS)
    clips_uploaded = 0

    for url in video_urls:
        if clips_uploaded >= CLIPS_PER_DAY:
            break

        print(f"Processing {url}")
        try:
            download_video(url)
        except Exception as e:
            print(f"Failed to download {url}: {e}")
            continue

        segments = transcribe_video()
        clips = select_clips(segments)
        random.shuffle(clips)

        for i, c in enumerate(clips):
            if clips_uploaded >= CLIPS_PER_DAY:
                break
            try:
                clip_file = cut_clip_ffmpeg(c["start"], c["end"], i)
                title = generate_title(c["text"])
                upload_to_youtube(clip_file, title)
                clips_uploaded += 1
            except Exception as e:
                print(f"Error processing clip: {e}")


if __name__ == "__main__":
    run()
