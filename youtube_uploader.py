"""Phase 5: YouTube投稿モジュール.

YouTube Data API v3を使って動画をアップロードする。
OAuth 2.0認証（初回ブラウザ認証、以後トークン再利用）。
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"


def load_config(config_path: Path = Path("config.json")) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def get_authenticated_service(
    client_secrets_file: str,
    token_file: str,
):
    """Build YouTube API service with OAuth 2.0 credentials."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    token_path = Path(token_file)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                client_secrets_file,
                YOUTUBE_SCOPES,
            )
            creds = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, credentials=creds)


def generate_metadata(
    date_str: str,
    config: dict,
    script_type: str = "daily",
    news_data: dict | None = None,
    topic: str = "",
) -> dict:
    """Generate video title, description, and tags."""
    channel = config.get("channel", {})
    yt_cfg = config.get("youtube", {})
    topic_label = topic if topic else "AI"

    if script_type == "daily":
        title = f"【{topic_label}ニュース】{date_str[:4]}/{date_str[4:6]}/{date_str[6:]}の{topic_label}ニュース｜{channel.get('name', 'ポンテのAI教室')} #AI #AIニュース #初心者 #ポンテのAIニュース"
        description = (
            f"🤖 {channel.get('name', 'ポンテのAI教室')}のデイリー{topic_label}ニュース！\n\n"
            f"今日の{topic_label}ニュースをポンテがわかりやすく紹介するよ！\n\n"
            f"📌 {channel.get('tagline', 'むずかしいAIを、かんたんに。')}\n\n"
        )
    else:
        title = f"【週間{topic_label}まとめ】今週の{topic_label}ニュースまとめ｜{channel.get('name', 'ポンテのAI教室')} #AI #AIニュース #初心者 #ポンテのAIニュース"
        description = (
            f"🤖 {channel.get('name', 'ポンテのAI教室')}のウィークリーまとめ！\n\n"
            f"今週の{topic_label}ニュースをポンテがまとめて紹介するよ！\n\n"
            f"📌 {channel.get('tagline', 'むずかしいAIを、かんたんに。')}\n\n"
        )

    # Add news source URLs if available
    if news_data and news_data.get("articles"):
        description += "📰 今日のニュース\n"
        for i, article in enumerate(news_data["articles"], 1):
            article_title = article.get("title_ja", article.get("title", ""))
            link = article.get("link", "")
            if link:
                description += f"{i}. {article_title}\n   {link}\n"
            else:
                description += f"{i}. {article_title}\n"
        description += "\n"

    if script_type == "daily":
        description += f"#{topic_label} #{topic_label}ニュース #ポンテのAI教室\n\n"
    else:
        description += f"#{topic_label} #{topic_label}ニュース #週間まとめ #ポンテのAI教室\n\n"

    description += (
        "---\n"
        "🔔 チャンネル登録よろしくね！\n\n"
        "運営：\n"
        "https://surc.online/"
    )

    tags = yt_cfg.get("default_tags", ["AI", "AIニュース"])
    if topic and topic != "AI":
        tags = [topic, f"{topic}ニュース"] + tags

    playlist = f"今日の{topic_label}ニュース（初心者向け）"

    return {
        "title": title[:100],
        "description": description,
        "tags": tags,
        "category_id": yt_cfg.get("default_category_id", "28"),
        "privacy": yt_cfg.get("initial_privacy", "private"),
        "playlist": playlist,
        "date": date_str,
    }


def generate_shorts_metadata(
    date_str: str,
    config: dict,
    news_data: dict | None = None,
    topic: str = "",
) -> dict:
    """Generate metadata for YouTube Shorts upload."""
    channel = config.get("channel", {})
    yt_cfg = config.get("youtube", {})
    topic_label = topic if topic else "AI"

    title = f"今日の{topic_label}ニュース {date_str[4:6]}/{date_str[6:]}｜{channel.get('name', 'ポンテのAI教室')} #AI #AIニュース #初心者 #ポンテのAIニュース #short #shorts #shortvideo #shortsvideo #shortfeed"

    description = (
        f"🤖 {channel.get('name', 'ポンテのAI教室')}のデイリー{topic_label}ニュース（ショート版）\n\n"
        f"📌 {channel.get('tagline', 'むずかしいAIを、かんたんに。')}\n\n"
    )

    if news_data and news_data.get("articles"):
        description += "📰 今日のニュース\n"
        for i, article in enumerate(news_data["articles"], 1):
            article_title = article.get("title_ja", article.get("title", ""))
            link = article.get("link", "")
            if link:
                description += f"{i}. {article_title}\n   {link}\n"
            else:
                description += f"{i}. {article_title}\n"
        description += "\n"

    description += (
        "▶ フル版はチャンネルでチェック！\n\n"
        f"#{topic_label} #{topic_label}ニュース #Shorts #ポンテのAI教室\n\n"
        "---\n"
        "🔔 チャンネル登録よろしくね！\n\n"
        "運営：\n"
        "https://surc.online/"
    )

    shorts_tags = yt_cfg.get("default_tags", ["AI", "AIニュース"]) + ["Shorts"]
    if topic and topic != "AI":
        shorts_tags = [topic, f"{topic}ニュース"] + shorts_tags

    playlist = f"今日の{topic_label}ニュース（初心者向け）"

    return {
        "title": title[:100],
        "description": description,
        "tags": shorts_tags,
        "category_id": yt_cfg.get("default_category_id", "28"),
        "privacy": yt_cfg.get("initial_privacy", "private"),
        "playlist": playlist,
        "date": date_str,
    }


def upload_video(
    video_path: Path,
    metadata: dict,
    config_path: Path = Path("config.json"),
) -> str:
    """Upload video to YouTube. Returns video URL."""
    from googleapiclient.http import MediaFileUpload

    config = load_config(config_path)
    yt_cfg = config.get("youtube", {})

    service = get_authenticated_service(
        yt_cfg["client_secrets_file"],
        yt_cfg["token_file"],
    )

    # recording date from metadata (YYYYMMDD → YYYY-MM-DD)
    date_str = metadata.get("date", "")
    recording_date = ""
    if len(date_str) == 8:
        recording_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}T00:00:00Z"

    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata["tags"],
            "categoryId": metadata["category_id"],
            "defaultLanguage": metadata.get("language", "ja"),
            "defaultAudioLanguage": metadata.get("language", "ja"),
        },
        "status": {
            "privacyStatus": metadata.get("privacy", "private"),
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": False,
        },
        "recordingDetails": {
            "recordingDate": recording_date,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,  # 10MB chunks
    )

    request = service.videos().insert(
        part="snippet,status,recordingDetails",
        body=body,
        media_body=media,
    )

    logger.info("Uploading: %s", video_path)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("Upload progress: %d%%", int(status.progress() * 100))

    video_id = response["id"]
    video_url = f"https://youtu.be/{video_id}"
    logger.info("Upload complete: %s", video_url)

    # Add to playlist if specified
    playlist_name = metadata.get("playlist")
    if playlist_name:
        _add_to_playlist(service, video_id, playlist_name)

    return video_url


def _find_or_create_playlist(service, title: str) -> str:
    """Find a playlist by title or create it. Returns playlist ID."""
    # Search existing playlists
    request = service.playlists().list(part="snippet", mine=True, maxResults=50)
    response = request.execute()
    for item in response.get("items", []):
        if item["snippet"]["title"] == title:
            logger.info("Found playlist: %s (%s)", title, item["id"])
            return item["id"]

    # Create new playlist
    body = {
        "snippet": {
            "title": title,
            "description": f"{title}の動画一覧",
        },
        "status": {"privacyStatus": "public"},
    }
    response = service.playlists().insert(part="snippet,status", body=body).execute()
    playlist_id = response["id"]
    logger.info("Created playlist: %s (%s)", title, playlist_id)
    return playlist_id


def _add_to_playlist(service, video_id: str, playlist_name: str) -> None:
    """Add a video to a playlist by name."""
    try:
        playlist_id = _find_or_create_playlist(service, playlist_name)
        body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id,
                },
            },
        }
        service.playlistItems().insert(part="snippet", body=body).execute()
        logger.info("Added video %s to playlist '%s'", video_id, playlist_name)
    except Exception as e:
        logger.warning("Failed to add to playlist '%s': %s", playlist_name, e)


def upload(
    date_str: str,
    config_path: Path = Path("config.json"),
    script_type: str = "daily",
    shorts: bool = False,
    topic: str = "",
) -> str:
    """Full upload flow. Returns video URL."""
    config = load_config(config_path)
    output_dir = Path(config["paths"]["output_dir"])
    state_dir = Path(config["paths"]["state_dir"])

    if shorts:
        video_path = output_dir / f"{date_str}_shorts.mp4"
    else:
        video_path = output_dir / f"{date_str}_{script_type}.mp4"

    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    # Load news data for URL inclusion in description
    news_data = None
    news_path = state_dir / f"news_{date_str}.json"
    if news_path.exists():
        with open(news_path, encoding="utf-8") as f:
            news_data = json.load(f)

    if shorts:
        metadata = generate_shorts_metadata(date_str, config, news_data=news_data, topic=topic)
    else:
        metadata = generate_metadata(date_str, config, script_type, news_data=news_data, topic=topic)
    return upload_video(video_path, metadata, config_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="YouTube Uploader")
    parser.add_argument("--date", required=True, help="Date string YYYYMMDD")
    parser.add_argument("--config", default="config.json", help="Config file path")
    parser.add_argument("--type", default="daily", choices=["daily", "weekly"])
    parser.add_argument("--shorts", action="store_true", help="Upload as YouTube Shorts")
    args = parser.parse_args()
    url = upload(args.date, Path(args.config), args.type, args.shorts)
    print(f"Uploaded: {url}")


if __name__ == "__main__":
    main()
