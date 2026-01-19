import os
import openai
import yt_dlp
import whisper
from moviepy.editor import VideoFileClip
from googleapiclient.discovery import build

openai.api_key = os.getenv("OPENAI_API_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

model = whisper.load_model("base")

def download_video(url):
    ydl_opts = {
        "outtmpl": "video.mp4",
        "format": "mp4"
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

def transcribe():
    result = model.transcribe("video.mp4")
    return result["segments"]

def find_best_clips(segments):
    text_blocks = []
    for s in segments:
        if 20 <= (s["end"] - s["start"]) <= 45:
            text_blocks.append({
                "start": s["start"],
                "end": s["end"],
                "text": s["text"]
            })

    prompt = f"""
    Pick the top 8 viral podcast moments about business, money, or success.
    Return JSON only with start and end timestamps.
    Data: {text_blocks}
    """

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )

    return eval(response.choices[0].message.content)

def cut_clip(start, end, index):
    clip = VideoFileClip("video.mp4").subclip(start, end)
    clip = clip.resize((1080, 1920))
    clip.write_videofile(f"clip_{index}.mp4", codec="libx264")

def upload_to_youtube(file, title):
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": "#business #money #podcast",
                "categoryId": "22"
            },
            "status": {
                "privacyStatus": "public"
            }
        },
        media_body=file
    )
    request.execute()

def generate_title(text):
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": f"Create a viral YouTube Shorts title: {text}"}]
    )
    return response.choices[0].message.content

def run():
    VIDEO_URL = "PASTE_VIDEO_URL_HERE"
    download_video(VIDEO_URL)
    segments = transcribe()
    clips = find_best_clips(segments)

    for i, c in enumerate(clips):
        cut_clip(c["start"], c["end"], i)
        title = generate_title(c["text"])
        upload_to_youtube(f"clip_{i}.mp4", title)

if __name__ == "__main__":
    run()
