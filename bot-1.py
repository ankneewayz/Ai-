#!/usr/bin/env python3
"""
================================================================================
 SongID Bot — Premium Telegram Song Recognition Bot  (v2)
 Owner: @ankneewayz
================================================================================

Accepts links (YouTube, TikTok, Instagram, Facebook, Twitter/X, Threads,
Reddit, Pinterest, Snapchat, SoundCloud, Vimeo, Dailymotion) OR direct
Telegram video/audio/voice uploads, downloads/reads the media, identifies
the song via a Shazam-style RapidAPI endpoint using a multi-segment retry
strategy, and replies with the media + a premium MarkdownV2 caption and
smart inline buttons.

Run:
    pip install -r requirements.txt
    cp .env.example .env   # fill in the values
    python bot.py

Requires `ffmpeg` and `ffprobe` on the system PATH.
================================================================================
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import logging.handlers
import os
import re
import shutil
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import aiosqlite
import yt_dlp
from dotenv import load_dotenv

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ==============================================================================
# CONFIGURATION
# ==============================================================================

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)) or default)
    except ValueError:
        return default


def _parse_id_list(raw: str) -> set:
    ids = set()
    for part in raw.replace(" ", "").split(","):
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                pass
    return ids


@dataclass
class Config:
    bot_token: str = field(default_factory=lambda: _env("BOT_TOKEN"))
    rapidapi_key: str = field(default_factory=lambda: _env("RAPIDAPI_KEY"))
    rapidapi_host: str = field(
        default_factory=lambda: _env(
            "RAPIDAPI_HOST", "shazam-song-recognition-api.p.rapidapi.com"
        )
    )
    cobalt_api: str = field(
        default_factory=lambda: _env(
            "COBALT_API", "https://cobalt-latest-gg37.onrender.com"
        )
    )
    owner_id: int = field(default_factory=lambda: _env_int("OWNER_ID", 0))
    admin_ids: set = field(default_factory=lambda: _parse_id_list(_env("ADMIN_IDS")))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    # --- new, "premium" tunables (all optional, sensible defaults) ---
    rate_limit_seconds: int = field(default_factory=lambda: _env_int("RATE_LIMIT_SECONDS", 15))
    max_concurrent_jobs: int = field(default_factory=lambda: _env_int("MAX_CONCURRENT_JOBS", 3))
    max_file_size_mb: int = field(default_factory=lambda: _env_int("MAX_FILE_SIZE_MB", 200))
    session_ttl_minutes: int = field(default_factory=lambda: _env_int("SESSION_TTL_MINUTES", 60))
    required_channel: str = field(default_factory=lambda: _env("REQUIRED_CHANNEL"))  # e.g. @mychannel

    def __post_init__(self):
        if self.owner_id:
            self.admin_ids.add(self.owner_id)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    def validate(self) -> List[str]:
        problems = []
        if not self.bot_token:
            problems.append("BOT_TOKEN is missing in .env")
        if not self.rapidapi_key:
            problems.append("RAPIDAPI_KEY is missing in .env")
        if not self.owner_id:
            problems.append("OWNER_ID is missing in .env")
        return problems


CFG = Config()

BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = BASE_DIR / "tmp"
SESSIONS_DIR = BASE_DIR / "sessions"
TMP_DIR.mkdir(exist_ok=True)
SESSIONS_DIR.mkdir(exist_ok=True)
DB_PATH = BASE_DIR / "songid_bot.db"
LOG_PATH = BASE_DIR / "bot.log"

MAX_TG_MSG = 3500

# ==============================================================================
# LOGGING
# ==============================================================================

logger = logging.getLogger("songid_bot")
logger.setLevel(getattr(logging, CFG.log_level.upper(), logging.INFO))

_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"))
_file = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
_file.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"))
logger.addHandler(_console)
logger.addHandler(_file)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("yt_dlp").setLevel(logging.WARNING)

# ==============================================================================
# PLATFORM DETECTION
# ==============================================================================

PLATFORM_PATTERNS: Dict[str, re.Pattern] = {
    "youtube": re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/\S+", re.IGNORECASE),
    "tiktok": re.compile(r"(https?://)?(www\.|vm\.|vt\.)?tiktok\.com/\S+", re.IGNORECASE),
    "instagram": re.compile(r"(https?://)?(www\.)?instagram\.com/\S+", re.IGNORECASE),
    "facebook": re.compile(r"(https?://)?(www\.|m\.|web\.)?(facebook\.com|fb\.watch)/\S+", re.IGNORECASE),
    "twitter": re.compile(r"(https?://)?(www\.)?(twitter\.com|x\.com)/\S+", re.IGNORECASE),
    "threads": re.compile(r"(https?://)?(www\.)?threads\.net/\S+", re.IGNORECASE),
    "reddit": re.compile(r"(https?://)?(www\.|old\.)?reddit\.com/\S+|https?://redd\.it/\S+", re.IGNORECASE),
    "pinterest": re.compile(r"(https?://)?(www\.)?(pinterest\.com|pin\.it)/\S+", re.IGNORECASE),
    "snapchat": re.compile(r"(https?://)?(www\.)?snapchat\.com/\S+", re.IGNORECASE),
    "soundcloud": re.compile(r"(https?://)?(www\.)?soundcloud\.com/\S+", re.IGNORECASE),
    "vimeo": re.compile(r"(https?://)?(www\.)?vimeo\.com/\S+", re.IGNORECASE),
    "dailymotion": re.compile(r"(https?://)?(www\.)?dailymotion\.com/\S+|https?://dai\.ly/\S+", re.IGNORECASE),
}

URL_REGEX = re.compile(r"https?://\S+", re.IGNORECASE)


def extract_url(text: str) -> Optional[str]:
    if not text:
        return None
    match = URL_REGEX.search(text.strip())
    return match.group(0) if match else None


def detect_platform(url: str) -> Optional[str]:
    for platform, pattern in PLATFORM_PATTERNS.items():
        if pattern.search(url):
            return platform
    return None


PLATFORM_EMOJI = {
    "youtube": "▶️", "tiktok": "🎵", "instagram": "📷", "facebook": "📘",
    "twitter": "🐦", "threads": "🧵", "reddit": "👽", "pinterest": "📌",
    "snapchat": "👻", "soundcloud": "☁️", "vimeo": "🎬", "dailymotion": "🎞",
    "telegram": "📎",
}

QUALITY_FORMATS = {
    "best": "best[ext=mp4]/best",
    "720p": "best[height<=720][ext=mp4]/best[height<=720]/best",
    "480p": "best[height<=480][ext=mp4]/best[height<=480]/best",
}

# ==============================================================================
# DATABASE
# ==============================================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    join_date TEXT NOT NULL,
    recognition_count INTEGER NOT NULL DEFAULT 0,
    download_count INTEGER NOT NULL DEFAULT 0,
    is_banned INTEGER NOT NULL DEFAULT 0,
    quality TEXT NOT NULL DEFAULT 'best'
);

CREATE TABLE IF NOT EXISTS cache (
    content_hash TEXT PRIMARY KEY,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS url_hash_map (
    url TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id INTEGER,
    url TEXT,
    source TEXT NOT NULL,
    media_kind TEXT NOT NULL,
    local_path TEXT,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT,
    artist TEXT,
    source_ref TEXT,
    recognized_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY,
    recognitions INTEGER NOT NULL DEFAULT 0,
    downloads INTEGER NOT NULL DEFAULT 0,
    new_users INTEGER NOT NULL DEFAULT 0
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = str(path)
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self):
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        # best-effort migration for DBs created by v1 of this bot
        for stmt in ("ALTER TABLE users ADD COLUMN quality TEXT NOT NULL DEFAULT 'best'",):
            try:
                await self._conn.execute(stmt)
            except aiosqlite.OperationalError:
                pass
        await self._conn.commit()
        logger.info("Database ready at %s", self.path)

    async def close(self):
        if self._conn:
            await self._conn.close()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def _bump_daily(self, field_name: str):
        assert field_name in ("recognitions", "downloads", "new_users")
        today = self._today()
        async with self._lock:
            await self._conn.execute(
                "INSERT INTO daily_stats (date, recognitions, downloads, new_users) "
                "VALUES (?, 0, 0, 0) ON CONFLICT(date) DO NOTHING",
                (today,),
            )
            await self._conn.execute(
                f"UPDATE daily_stats SET {field_name} = {field_name} + 1 WHERE date = ?",
                (today,),
            )
            await self._conn.commit()

    async def today_stats(self) -> Dict[str, int]:
        cur = await self._conn.execute(
            "SELECT recognitions, downloads, new_users FROM daily_stats WHERE date = ?",
            (self._today(),),
        )
        row = await cur.fetchone()
        if not row:
            return {"recognitions": 0, "downloads": 0, "new_users": 0}
        return dict(row)

    # ---- users ---------------------------------------------------------------

    async def touch_user(self, user_id: int, username: Optional[str]) -> bool:
        async with self._lock:
            cur = await self._conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            row = await cur.fetchone()
            if row:
                await self._conn.execute(
                    "UPDATE users SET username = ? WHERE user_id = ?", (username, user_id)
                )
                await self._conn.commit()
                return False
            await self._conn.execute(
                "INSERT INTO users (user_id, username, join_date) VALUES (?, ?, ?)",
                (user_id, username, datetime.now(timezone.utc).isoformat()),
            )
            await self._conn.commit()
        await self._bump_daily("new_users")
        return True

    async def is_banned(self, user_id: int) -> bool:
        cur = await self._conn.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return bool(row["is_banned"]) if row else False

    async def set_banned(self, user_id: int, banned: bool):
        async with self._lock:
            await self._conn.execute(
                "UPDATE users SET is_banned = ? WHERE user_id = ?", (1 if banned else 0, user_id)
            )
            await self._conn.commit()

    async def set_quality(self, user_id: int, quality: str):
        async with self._lock:
            await self._conn.execute(
                "UPDATE users SET quality = ? WHERE user_id = ?", (quality, user_id)
            )
            await self._conn.commit()

    async def get_quality(self, user_id: int) -> str:
        cur = await self._conn.execute("SELECT quality FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row["quality"] if row and row["quality"] else "best"

    async def increment(self, user_id: int, field_name: str):
        assert field_name in ("recognition_count", "download_count")
        async with self._lock:
            await self._conn.execute(
                f"UPDATE users SET {field_name} = {field_name} + 1 WHERE user_id = ?", (user_id,)
            )
            await self._conn.commit()
        await self._bump_daily("recognitions" if field_name == "recognition_count" else "downloads")

    async def all_user_ids(self) -> List[int]:
        cur = await self._conn.execute("SELECT user_id FROM users WHERE is_banned = 0")
        rows = await cur.fetchall()
        return [r["user_id"] for r in rows]

    async def stats(self) -> Dict[str, int]:
        cur = await self._conn.execute(
            "SELECT COUNT(*) AS c, SUM(recognition_count) AS r, "
            "SUM(download_count) AS d, SUM(is_banned) AS b FROM users"
        )
        row = await cur.fetchone()
        return {
            "total_users": row["c"] or 0,
            "total_recognitions": row["r"] or 0,
            "total_downloads": row["d"] or 0,
            "banned_users": row["b"] or 0,
        }

    async def leaderboard(self, limit: int = 10) -> List[dict]:
        cur = await self._conn.execute(
            "SELECT user_id, username, recognition_count FROM users "
            "WHERE is_banned = 0 ORDER BY recognition_count DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ---- content-hash cache ----------------------------------------------------

    async def get_cache_by_hash(self, content_hash: str) -> Optional[dict]:
        cur = await self._conn.execute(
            "SELECT metadata_json FROM cache WHERE content_hash = ?", (content_hash,)
        )
        row = await cur.fetchone()
        return json.loads(row["metadata_json"]) if row else None

    async def set_cache_by_hash(self, content_hash: str, metadata: dict):
        async with self._lock:
            await self._conn.execute(
                "INSERT INTO cache (content_hash, metadata_json, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(content_hash) DO UPDATE SET metadata_json = excluded.metadata_json",
                (content_hash, json.dumps(metadata), datetime.now(timezone.utc).isoformat()),
            )
            await self._conn.commit()

    async def get_url_hash(self, url: str) -> Optional[str]:
        cur = await self._conn.execute("SELECT content_hash FROM url_hash_map WHERE url = ?", (url,))
        row = await cur.fetchone()
        return row["content_hash"] if row else None

    async def set_url_hash(self, url: str, content_hash: str):
        async with self._lock:
            await self._conn.execute(
                "INSERT INTO url_hash_map (url, content_hash, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(url) DO UPDATE SET content_hash = excluded.content_hash, "
                "updated_at = excluded.updated_at",
                (url, content_hash, datetime.now(timezone.utc).isoformat()),
            )
            await self._conn.commit()

    async def cache_count(self) -> int:
        cur = await self._conn.execute("SELECT COUNT(*) AS c FROM cache")
        row = await cur.fetchone()
        return row["c"] or 0

    async def clear_cache(self) -> int:
        n = await self.cache_count()
        async with self._lock:
            await self._conn.execute("DELETE FROM cache")
            await self._conn.execute("DELETE FROM url_hash_map")
            await self._conn.commit()
        return n

    # ---- sessions (drive inline-button callbacks; hold media briefly) ---------

    async def save_session(
        self,
        user_id: int,
        url: Optional[str],
        source: str,
        media_kind: str,
        metadata: dict,
        local_path: Optional[str],
    ) -> str:
        session_id = uuid.uuid4().hex[:12]
        async with self._lock:
            await self._conn.execute(
                "INSERT INTO sessions (session_id, user_id, url, source, media_kind, "
                "local_path, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id, user_id, url, source, media_kind, local_path,
                    json.dumps(metadata), datetime.now(timezone.utc).isoformat(),
                ),
            )
            await self._conn.commit()
        return session_id

    async def get_session(self, session_id: str) -> Optional[dict]:
        cur = await self._conn.execute(
            "SELECT user_id, url, source, media_kind, local_path, metadata_json, created_at "
            "FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        return {
            "user_id": row["user_id"],
            "url": row["url"],
            "source": row["source"],
            "media_kind": row["media_kind"],
            "local_path": row["local_path"],
            "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
        }

    async def purge_expired_sessions(self, ttl_minutes: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=ttl_minutes)).isoformat()
        cur = await self._conn.execute(
            "SELECT session_id, local_path FROM sessions WHERE created_at < ?", (cutoff,)
        )
        rows = await cur.fetchall()
        async with self._lock:
            await self._conn.execute("DELETE FROM sessions WHERE created_at < ?", (cutoff,))
            await self._conn.commit()
        for r in rows:
            if r["local_path"]:
                cleanup_paths(Path(r["local_path"]))
        return len(rows)

    # ---- history ---------------------------------------------------------------

    async def add_history(self, user_id: int, title: str, artist: str, source_ref: str):
        async with self._lock:
            await self._conn.execute(
                "INSERT INTO history (user_id, title, artist, source_ref, recognized_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, title, artist, source_ref, datetime.now(timezone.utc).isoformat()),
            )
            await self._conn.commit()

    async def get_history(self, user_id: int, limit: int = 10) -> List[dict]:
        cur = await self._conn.execute(
            "SELECT title, artist, recognized_at FROM history WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


db = Database(DB_PATH)

# ==============================================================================
# HELPERS
# ==============================================================================


def esc(text: Any) -> str:
    if text is None:
        return "N/A"
    text = str(text)
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", text)


def human_duration(seconds: Any) -> str:
    try:
        seconds = int(float(seconds))
    except (TypeError, ValueError):
        return "N/A"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def safe_edit(message: Message, text: str, **kwargs):
    try:
        await message.edit_text(text, **kwargs)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.debug("safe_edit failed: %s", e)
    except TelegramError as e:
        logger.debug("safe_edit telegram error: %s", e)


async def safe_delete(message: Message):
    try:
        await message.delete()
    except TelegramError:
        pass


def cleanup_paths(*paths: Optional[Path]):
    for p in paths:
        if not p:
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink()
        except OSError:
            pass


def chunk_text_by_lines(text: str, limit: int = MAX_TG_MSG) -> List[str]:
    """Split text into <=limit chunks on line boundaries so escape sequences
    (e.g. '\\.' from MarkdownV2 escaping) never get split across chunks."""
    lines = text.split("\n")
    chunks, current = [], ""
    for line in lines:
        candidate = (current + "\n" + line) if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


class BotError(Exception):
    """User-facing friendly error."""


class NoMatchError(BotError):
    """Recognition ran fine but found no matching song."""


async def check_membership(bot, user_id: int) -> bool:
    if not CFG.required_channel:
        return True
    try:
        member = await bot.get_chat_member(CFG.required_channel, user_id)
        return member.status in ("member", "administrator", "creator")
    except TelegramError:
        # If the bot can't check (not an admin in the channel, etc.) fail open
        # rather than lock everyone out.
        logger.warning("Could not verify channel membership for %s", user_id)
        return True


def join_gate_keyboard() -> InlineKeyboardMarkup:
    channel = CFG.required_channel.lstrip("@")
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{channel}")],
            [InlineKeyboardButton("✅ I've Joined", callback_data="checkjoin:_")],
        ]
    )


# ==============================================================================
# DOWNLOADER (yt-dlp for YouTube, Cobalt for everything else)
# ==============================================================================


@dataclass
class DownloadResult:
    video_path: Path
    title: str
    source: str  # "youtube" | "cobalt"
    work_dir: Path


class MediaDownloader:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def download(self, url: str, platform: str, quality: str = "best") -> DownloadResult:
        work_dir = TMP_DIR / uuid.uuid4().hex[:10]
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            if platform == "youtube":
                return await self._download_youtube(url, work_dir, quality)
            return await self._download_cobalt(url, work_dir, quality)
        except Exception:
            cleanup_paths(work_dir)
            raise

    # ---- YouTube via yt-dlp -------------------------------------------------

    async def _download_youtube(self, url: str, work_dir: Path, quality: str) -> DownloadResult:
        fmt = QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"])
        loop = asyncio.get_running_loop()

        # Pre-flight size check (avoids downloading huge files just to reject them)
        def _probe():
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "format": fmt}) as ydl:
                info = ydl.extract_info(url, download=False)
                if "entries" in info:
                    info = info["entries"][0]
                return info

        try:
            info = await loop.run_in_executor(None, _probe)
        except yt_dlp.utils.DownloadError as e:
            raise BotError(f"yt-dlp couldn't read this video: {e}") from e

        size = info.get("filesize") or info.get("filesize_approx")
        if size and size > CFG.max_file_size_mb * 1024 * 1024:
            raise BotError(f"Video is too large ({size / 1e6:.0f}MB, limit {CFG.max_file_size_mb}MB).")

        outtmpl = str(work_dir / "%(id)s.%(ext)s")
        ydl_opts = {
            "format": fmt,
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "max_filesize": CFG.max_file_size_mb * 1024 * 1024,
            "retries": 3,
            "socket_timeout": 30,
        }

        def _run() -> Tuple[Path, str]:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                dl_info = ydl.extract_info(url, download=True)
                if "entries" in dl_info:
                    dl_info = dl_info["entries"][0]
                filepath = Path(ydl.prepare_filename(dl_info))
                return filepath, dl_info.get("title") or "Unknown"

        try:
            filepath, title = await loop.run_in_executor(None, _run)
        except yt_dlp.utils.DownloadError as e:
            raise BotError(f"yt-dlp couldn't download this video: {e}") from e

        if not filepath.exists():
            raise BotError("Download finished but the file is missing.")

        return DownloadResult(video_path=filepath, title=title, source="youtube", work_dir=work_dir)

    async def _download_youtube_audio(self, url: str, work_dir: Path) -> Path:
        outtmpl = str(work_dir / "%(id)s.%(ext)s")
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "retries": 3,
            "socket_timeout": 30,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ],
        }

        def _run() -> Path:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if "entries" in info:
                    info = info["entries"][0]
                base = Path(ydl.prepare_filename(info))
                return base.with_suffix(".mp3")

        loop = asyncio.get_running_loop()
        try:
            mp3_path = await loop.run_in_executor(None, _run)
        except yt_dlp.utils.DownloadError as e:
            raise BotError(f"yt-dlp couldn't extract audio: {e}") from e
        if not mp3_path.exists():
            raise BotError("Audio extraction finished but MP3 file is missing.")
        return mp3_path

    # ---- Everything else via Cobalt ----------------------------------------

    async def _cobalt_request(self, url: str, audio_only: bool = False, quality: str = "best") -> dict:
        endpoint = CFG.cobalt_api.rstrip("/") + "/"
        payload: Dict[str, Any] = {"url": url}
        if audio_only:
            payload["downloadMode"] = "audio"
        # Best-effort quality hint; ignored by Cobalt instances that don't support it.
        if quality in ("720p", "480p"):
            payload["videoQuality"] = quality.rstrip("p")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        try:
            async with self.session.post(
                endpoint, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=40)
            ) as resp:
                if resp.status >= 500:
                    raise BotError("The Cobalt downloader service is currently offline.")
                data = await resp.json(content_type=None)
        except asyncio.TimeoutError as e:
            raise BotError("The downloader service timed out. Please try again.") from e
        except aiohttp.ClientError as e:
            raise BotError("Couldn't reach the downloader service.") from e

        status = data.get("status")
        if status in ("tunnel", "redirect", "stream"):
            return {"url": data.get("url"), "filename": data.get("filename")}
        if status == "picker":
            items = data.get("picker") or []
            if not items:
                raise BotError("No downloadable media found at that link.")
            return {"url": items[0].get("url"), "filename": None}
        if status == "error":
            err = data.get("error", {})
            code = err.get("code", "unknown_error") if isinstance(err, dict) else str(err)
            raise BotError(f"Downloader couldn't process this link ({code}).")
        raise BotError("Unrecognized response from the downloader service.")

    async def _download_cobalt(self, url: str, work_dir: Path, quality: str) -> DownloadResult:
        info = await self._cobalt_request(url, audio_only=False, quality=quality)
        file_url = info.get("url")
        if not file_url:
            raise BotError("Downloader did not return a media URL.")

        await self._precheck_size(file_url)

        filename = info.get("filename") or f"{uuid.uuid4().hex[:8]}.mp4"
        dest = work_dir / filename
        await self._fetch_to_file(file_url, dest)

        return DownloadResult(video_path=dest, title=dest.stem, source="cobalt", work_dir=work_dir)

    async def _download_cobalt_audio(self, url: str, work_dir: Path) -> Optional[Path]:
        try:
            info = await self._cobalt_request(url, audio_only=True)
        except BotError:
            return None
        file_url = info.get("url")
        if not file_url:
            return None
        filename = info.get("filename") or f"{uuid.uuid4().hex[:8]}.mp3"
        dest = work_dir / filename
        try:
            await self._fetch_to_file(file_url, dest)
        except BotError:
            return None
        return dest

    async def _precheck_size(self, file_url: str):
        """Best-effort HEAD check; silently skipped if the server doesn't support it."""
        try:
            async with self.session.head(file_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                cl = resp.headers.get("Content-Length")
                if cl and int(cl) > CFG.max_file_size_mb * 1024 * 1024:
                    raise BotError(
                        f"File is too large ({int(cl) / 1e6:.0f}MB, limit {CFG.max_file_size_mb}MB)."
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass  # fall back to the streaming size guard below

    async def _fetch_to_file(self, file_url: str, dest: Path, status_cb=None):
        max_bytes = CFG.max_file_size_mb * 1024 * 1024
        try:
            async with self.session.get(file_url, timeout=aiohttp.ClientTimeout(total=180)) as resp:
                if resp.status != 200:
                    raise BotError(f"Failed to fetch media (HTTP {resp.status}).")
                total = int(resp.headers.get("Content-Length") or 0)
                size = 0
                last_report = 0.0
                with open(dest, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 256):
                        size += len(chunk)
                        if size > max_bytes:
                            raise BotError("File is too large to process.")
                        f.write(chunk)
                        if status_cb and total:
                            now = time.monotonic()
                            if now - last_report > 2.0:
                                last_report = now
                                await status_cb(min(99, int(size / total * 100)))
        except asyncio.TimeoutError as e:
            raise BotError("Timed out while downloading the media file.") from e
        except aiohttp.ClientError as e:
            raise BotError("Network error while downloading the media file.") from e


# ==============================================================================
# FFMPEG / FFPROBE HELPERS
# ==============================================================================


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


async def get_duration_seconds(path: Path) -> Optional[float]:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, _ = await proc.communicate()
        return float(out.decode().strip())
    except (OSError, ValueError):
        return None


async def extract_audio_segment(video_path: Path, out_path: Path, start: float, duration: float) -> Path:
    if not ffmpeg_available():
        raise BotError("FFmpeg is not installed on the server.")
    cmd = [
        "ffmpeg", "-y", "-ss", str(max(0, start)), "-i", str(video_path),
        "-t", str(max(3, duration)), "-vn", "-ac", "1", "-ar", "44100",
        "-f", "mp3", "-q:a", "4", str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not out_path.exists():
        raise BotError(f"FFmpeg failed to extract audio: {stderr.decode(errors='ignore')[-300:]}")
    return out_path


async def extract_full_audio(video_path: Path, out_path: Path) -> Path:
    if not ffmpeg_available():
        raise BotError("FFmpeg is not installed on the server.")
    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-b:a", "192k", str(out_path)]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not out_path.exists():
        raise BotError(f"FFmpeg failed to extract audio: {stderr.decode(errors='ignore')[-300:]}")
    return out_path


async def extract_thumbnail(video_path: Path, out_path: Path) -> Optional[Path]:
    if not ffmpeg_available():
        return None
    cmd = [
        "ffmpeg", "-y", "-ss", "1", "-i", str(video_path),
        "-frames:v", "1", "-vf", "scale=320:-1", str(out_path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
        return out_path if out_path.exists() else None
    except OSError:
        return None


def build_segments(duration: Optional[float]) -> List[Tuple[float, float]]:
    """Build a list of (start, length) audio windows to try recognition on,
    preferring a point past any intro/silence, then progressively longer
    clips, then a fallback from the very start of the clip."""
    if not duration or duration <= 0:
        return [(0, 20)]
    start = 30 if duration > 32 else 0
    segments: List[Tuple[float, float]] = []
    for length in (20, 60, 90):
        remaining = duration - start
        if remaining < 5:
            break
        segments.append((start, min(length, remaining)))
    if start != 0:
        segments.append((0, min(20, duration)))
    # de-duplicate while preserving order
    seen = set()
    deduped = []
    for seg in segments:
        key = (round(seg[0], 1), round(seg[1], 1))
        if key not in seen:
            seen.add(key)
            deduped.append(seg)
    return deduped or [(0, min(20, duration))]


# ==============================================================================
# SHAZAM RECOGNITION (RapidAPI)
# ==============================================================================


class Recognizer:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.url = f"https://{CFG.rapidapi_host}/recognize/file"

    async def recognize_bytes(self, data: bytes) -> dict:
        headers = {
            "X-RapidAPI-Key": CFG.rapidapi_key,
            "X-RapidAPI-Host": CFG.rapidapi_host,
            "Content-Type": "application/octet-stream",
        }
        start = time.monotonic()
        try:
            async with self.session.post(
                self.url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status in (408, 504):
                    raise BotError("Recognition service timed out. Please try again.")
                if resp.status == 429:
                    raise BotError("Recognition rate limit reached. Try again shortly.")
                if resp.status >= 400:
                    raise BotError(f"Recognition API error (HTTP {resp.status}).")
                raw = await resp.json(content_type=None)
        except asyncio.TimeoutError as e:
            raise BotError("Recognition request timed out.") from e
        except aiohttp.ClientError as e:
            raise BotError("Couldn't reach the recognition service.") from e

        elapsed = round(time.monotonic() - start, 2)
        parsed = self._parse(raw)
        if not parsed:
            raise NoMatchError("No matching song was found for this audio segment.")
        parsed["_recognition_time"] = elapsed
        return parsed

    @staticmethod
    def _parse(raw: dict) -> Optional[dict]:
        if not raw:
            return None
        track = raw.get("track") or raw.get("result") or raw.get("data") or raw
        if not track or not isinstance(track, dict):
            return None

        title = track.get("title") or track.get("name")
        subtitle = track.get("subtitle") or track.get("artist")
        if not title and not subtitle:
            return None

        sections = track.get("sections") or []
        genre = None
        release_date = None
        label = None
        isrc = track.get("isrc")
        explicit = track.get("explicit") or (track.get("hub") or {}).get("explicit")

        for section in sections:
            if not isinstance(section, dict):
                continue
            for meta in section.get("metadata", []) or []:
                title_field = (meta.get("title") or "").lower()
                if "genre" in title_field:
                    genre = meta.get("text")
                elif "release" in title_field:
                    release_date = meta.get("text")
                elif "label" in title_field:
                    label = meta.get("text")

        if not genre and isinstance(track.get("genres"), dict):
            genre = track["genres"].get("primary")

        images = track.get("images") or {}
        artwork = images.get("coverarthq") or images.get("coverart") or images.get("background")

        hub = track.get("hub") or {}
        providers = hub.get("providers") or []
        apple_music_url = None
        spotify_url = None
        for action in hub.get("actions") or []:
            uri = action.get("uri") if isinstance(action, dict) else None
            if uri and uri.startswith("https://music.apple.com"):
                apple_music_url = uri

        for provider in providers:
            if not isinstance(provider, dict):
                continue
            caption = (provider.get("caption") or "").lower()
            for action in provider.get("actions", []) or []:
                uri = action.get("uri")
                if not uri:
                    continue
                if "spotify" in caption or "spotify" in uri:
                    spotify_url = uri
                elif "apple" in caption or "music.apple" in uri:
                    apple_music_url = apple_music_url or uri

        youtube_url = track.get("url") if track.get("url") and "youtube" in str(track.get("url")) else None

        lyrics = None
        sec_lyrics = next((s for s in sections if isinstance(s, dict) and s.get("type") == "LYRICS"), None)
        if sec_lyrics:
            lyrics_lines = sec_lyrics.get("text") or []
            if lyrics_lines:
                lyrics = "\n".join(lyrics_lines)

        return {
            "title": title or "Unknown",
            "artist": subtitle or "Unknown",
            "album": track.get("album"),
            "release_date": release_date,
            "genre": genre,
            "label": label,
            "duration": (track.get("durationms", 0) / 1000) if track.get("durationms") else track.get("duration"),
            "isrc": isrc,
            "explicit": bool(explicit),
            "artwork": artwork,
            "spotify_url": spotify_url,
            "apple_music_url": apple_music_url,
            "youtube_url": youtube_url,
            "lyrics": lyrics,
            "shazam_url": track.get("url"),
        }


async def recognize_multi_segment(
    media_path: Path, work_dir: Path, recognizer: Recognizer
) -> Tuple[dict, str]:
    """Try progressively larger/later audio windows until a match is found.
    Returns (metadata, content_hash). Raises BotError if nothing matches
    or a non-recoverable error occurs."""
    duration = await get_duration_seconds(media_path)
    segments = build_segments(duration)

    last_hash = None
    for i, (start, length) in enumerate(segments):
        clip_path = work_dir / f"seg_{i}.mp3"
        try:
            await extract_audio_segment(media_path, clip_path, start, length)
        except BotError:
            continue  # try the next window

        audio_bytes = clip_path.read_bytes()
        content_hash = hashlib.sha256(audio_bytes).hexdigest()
        last_hash = content_hash

        cached = await db.get_cache_by_hash(content_hash)
        if cached:
            return cached, content_hash

        try:
            metadata = await recognizer.recognize_bytes(audio_bytes)
            await db.set_cache_by_hash(content_hash, metadata)
            return metadata, content_hash
        except NoMatchError:
            continue  # try the next, longer/later window
        # any other BotError (network, rate-limit, etc.) propagates immediately

    raise NoMatchError(
        f"No matching song found after trying {len(segments)} audio segment(s)."
    ) if last_hash else NoMatchError("Could not extract any audio to recognize.")


# ==============================================================================
# CAPTION + KEYBOARD BUILDERS
# ==============================================================================


def build_caption(meta: dict) -> str:
    lines = [
        "🎵 *Song Recognized*",
        "",
        f"🎼 *Title:* {esc(meta.get('title'))}",
        f"👤 *Artist:* {esc(meta.get('artist'))}",
        f"💿 *Album:* {esc(meta.get('album') or 'N/A')}",
        f"📅 *Release Date:* {esc(meta.get('release_date') or 'N/A')}",
        f"🎶 *Genre:* {esc(meta.get('genre') or 'N/A')}",
        f"⏱ *Duration:* {esc(human_duration(meta.get('duration')))}",
        f"🌍 *ISRC:* {esc(meta.get('isrc') or 'N/A')}",
        f"⭐ *Explicit:* {esc('Yes' if meta.get('explicit') else 'No')}",
        f"⚡ *Recognition Time:* {esc(meta.get('_recognition_time', 'N/A'))}s",
    ]
    return "\n".join(lines)


def build_keyboard(meta: dict, session_id: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton("🎧 Download MP3", callback_data=f"mp3:{session_id}")]
    ]

    row2 = []
    if meta.get("spotify_url"):
        row2.append(InlineKeyboardButton("🎵 Spotify", url=meta["spotify_url"]))
    if meta.get("youtube_url"):
        row2.append(InlineKeyboardButton("▶️ YouTube", url=meta["youtube_url"]))
    if meta.get("apple_music_url"):
        row2.append(InlineKeyboardButton("🍎 Apple Music", url=meta["apple_music_url"]))
    if row2:
        rows.append(row2)

    row3 = []
    if meta.get("lyrics"):
        row3.append(InlineKeyboardButton("📝 Lyrics", callback_data=f"lyrics:{session_id}"))
    row3.append(InlineKeyboardButton("🔄 Recognize Again", callback_data=f"again:{session_id}"))
    rows.append(row3)

    rows.append([InlineKeyboardButton("📋 Copy Song Name", callback_data=f"copy:{session_id}")])
    return InlineKeyboardMarkup(rows)


# ==============================================================================
# CORE PROCESSING PIPELINE (shared by URL flow and direct-upload flow)
# ==============================================================================


async def run_pipeline(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status: Message,
    media_path: Path,
    work_dir: Path,
    source: str,
    media_kind: str,
    url: Optional[str],
    force_refresh: bool = False,
):
    user = update.effective_user
    downloader: MediaDownloader = context.bot_data["downloader"]
    recognizer: Recognizer = context.bot_data["recognizer"]
    semaphore: asyncio.Semaphore = context.bot_data["job_semaphore"]

    if semaphore.locked():
        await safe_edit(status, "⏳ Queue is busy — waiting for a free processing slot...")

    async with semaphore:
        preserved_path: Optional[Path] = None
        try:
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

            cached_hash = None if force_refresh else (await db.get_url_hash(url) if url else None)
            cached_meta = await db.get_cache_by_hash(cached_hash) if cached_hash else None

            if cached_meta:
                metadata, content_hash = cached_meta, cached_hash
            else:
                await safe_edit(status, "🧠 Recognizing...")
                metadata, content_hash = await recognize_multi_segment(media_path, work_dir, recognizer)
                if url:
                    await db.set_url_hash(url, content_hash)

            await safe_edit(status, "📤 Uploading...")

            # Preserve the original media for a limited time so MP3 / Recognize
            # Again work later without re-downloading (esp. for direct uploads
            # that have no source URL to fall back to).
            preserved_path = SESSIONS_DIR / f"{uuid.uuid4().hex[:12]}{media_path.suffix}"
            shutil.copy2(media_path, preserved_path)

            session_id = await db.save_session(
                user.id, url, source, media_kind, metadata, str(preserved_path)
            )
            caption = build_caption(metadata)
            keyboard = build_keyboard(metadata, session_id)

            if media_kind == "video":
                size = media_path.stat().st_size
                if size > 49 * 1024 * 1024:
                    raise BotError("The video is too large to send via Telegram bots (>50MB).")
                thumb_path = await extract_thumbnail(media_path, work_dir / "thumb.jpg")
                with open(media_path, "rb") as vf:
                    thumb_file = open(thumb_path, "rb") if thumb_path else None
                    try:
                        await update.effective_message.reply_video(
                            video=vf,
                            caption=caption,
                            parse_mode=ParseMode.MARKDOWN_V2,
                            reply_markup=keyboard,
                            supports_streaming=True,
                            thumbnail=thumb_file,
                        )
                    finally:
                        if thumb_file:
                            thumb_file.close()
            else:
                # audio / voice input: no video to resend, so send the audio
                # itself back with the caption.
                with open(media_path, "rb") as af:
                    await update.effective_message.reply_audio(
                        audio=af,
                        caption=caption,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=keyboard,
                        title=metadata.get("title"),
                        performer=metadata.get("artist"),
                    )

            if metadata.get("artwork"):
                try:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=metadata["artwork"],
                        caption="🖼 Album Artwork",
                    )
                except TelegramError:
                    pass  # artwork is a nice-to-have, never fail the request over it

            await db.increment(user.id, "recognition_count")
            await db.add_history(user.id, metadata.get("title", "Unknown"), metadata.get("artist", "Unknown"), url or "telegram upload")
            await safe_delete(status)

        except BotError as e:
            await safe_edit(status, f"❌ {esc(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)
        except RetryAfter as e:
            await safe_edit(status, f"⏳ Telegram flood control: retry after {e.retry_after}s.")
        except TimedOut:
            await safe_edit(status, "⌛ Telegram request timed out. Please try again.")
        except TelegramError as e:
            logger.exception("Telegram error while processing")
            await safe_edit(status, f"❌ Telegram error: {esc(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)
        except Exception:
            logger.exception("Unhandled error while processing")
            await safe_edit(status, "❌ Something went wrong. Please try again later.")
        finally:
            cleanup_paths(work_dir)


async def process_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, force_refresh: bool = False):
    user = update.effective_user

    if await db.is_banned(user.id):
        await update.effective_message.reply_text("🚫 You are banned from using this bot.")
        return

    platform = detect_platform(url)
    if not platform:
        await update.effective_message.reply_text(
            "⚠️ Unsupported or invalid link.\n\n"
            "Supported: YouTube, TikTok, Instagram, Facebook, Twitter/X, Threads, "
            "Reddit, Pinterest, Snapchat, SoundCloud, Vimeo, Dailymotion — or just "
            "send me a video/audio/voice file directly."
        )
        return

    status = await update.effective_message.reply_text(f"{PLATFORM_EMOJI.get(platform, '🔍')} Detecting platform...")
    downloader: MediaDownloader = context.bot_data["downloader"]

    try:
        await safe_edit(status, "⬇ Downloading...")
        quality = await db.get_quality(user.id)
        result = await downloader.download(url, platform, quality)
    except BotError as e:
        await safe_edit(status, f"❌ {esc(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)
        return
    except Exception:
        logger.exception("Download failed for %s", url)
        await safe_edit(status, "❌ Something went wrong while downloading. Please try again later.")
        return

    await run_pipeline(
        update, context, status, result.video_path, result.work_dir,
        source=result.source, media_kind="video", url=url, force_refresh=force_refresh,
    )


async def send_mp3(update: Update, context: ContextTypes.DEFAULT_TYPE, session: dict):
    query = update.callback_query
    user = update.effective_user
    url = session["url"]
    source = session["source"]
    meta = session["metadata"]
    local_path = Path(session["local_path"]) if session["local_path"] else None

    downloader: MediaDownloader = context.bot_data["downloader"]
    work_dir = TMP_DIR / uuid.uuid4().hex[:10]
    work_dir.mkdir(parents=True, exist_ok=True)

    status_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="🎧 Preparing MP3...")
    mp3_path: Optional[Path] = None
    try:
        # Fast path: we still have the original media preserved locally.
        if local_path and local_path.exists():
            mp3_path = await extract_full_audio(local_path, work_dir / "audio.mp3")
        elif source == "youtube" and url:
            mp3_path = await downloader._download_youtube_audio(url, work_dir)
        elif url:
            mp3_path = await downloader._download_cobalt_audio(url, work_dir)
            if not mp3_path:
                dl = await downloader.download(url, detect_platform(url) or "cobalt")
                try:
                    mp3_path = await extract_full_audio(dl.video_path, work_dir / "audio.mp3")
                finally:
                    cleanup_paths(dl.work_dir)
        else:
            raise BotError("The original media has expired and can no longer be retrieved.")

        if not mp3_path or not mp3_path.exists():
            raise BotError("Couldn't produce an MP3 for this track.")

        title = meta.get("title", "song")
        artist = meta.get("artist", "")
        with open(mp3_path, "rb") as af:
            await context.bot.send_audio(
                chat_id=update.effective_chat.id, audio=af, title=title, performer=artist,
                caption=f"🎧 {esc(title)} — {esc(artist)}", parse_mode=ParseMode.MARKDOWN_V2,
            )
        await db.increment(user.id, "download_count")
        await safe_delete(status_msg)
    except BotError as e:
        await safe_edit(status_msg, f"❌ {esc(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception:
        logger.exception("Error producing MP3 for session")
        await safe_edit(status_msg, "❌ Failed to prepare MP3. Please try again.")
    finally:
        cleanup_paths(work_dir)


# ==============================================================================
# COMMAND HANDLERS
# ==============================================================================


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = await db.touch_user(user.id, user.username)
    text = (
        "🎵 *Welcome to SongID Bot\\!*\n\n"
        "Send me a link \\(YouTube, TikTok, Instagram, Facebook, Twitter/X, Threads, "
        "Reddit, Pinterest, Snapchat, SoundCloud, Vimeo, Dailymotion\\) — or just send "
        "a video, audio, or voice message directly — and I'll identify the song 🎧\n\n"
        "Use /help to see everything I can do\\."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
    if is_new:
        logger.info("New user: %s (%s)", user.id, user.username)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "*📖 How to use SongID Bot*\n\n"
        "1️⃣ Send a supported link, or a video/audio/voice file directly\n"
        "2️⃣ I'll detect the song using multiple audio segments for accuracy\n"
        "3️⃣ You get the media back with full song info\n\n"
        "*Commands*\n"
        "/start — welcome message\n"
        "/help — this message\n"
        "/stats — bot usage stats\n"
        "/history — your last recognitions\n"
        "/leaderboard — most active users\n"
        "/settings — change preferred video quality\n"
        "/ping — check bot latency\n"
        "/about — about this bot\n\n"
        "*Buttons on results*\n"
        "🎧 Download MP3 — get just the audio\n"
        "🎵 Spotify / ▶️ YouTube / 🍎 Apple Music — open the song\n"
        "📝 Lyrics — view lyrics if available\n"
        "🔄 Recognize Again — force a fresh recognition\n"
        "📋 Copy Song Name — get title \\+ artist as text"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = await db.stats()
    t = await db.today_stats()
    cache_n = await db.cache_count()
    text = (
        "*📊 Bot Statistics*\n\n"
        f"👥 Users: {esc(s['total_users'])}\n"
        f"🎧 Recognitions: {esc(s['total_recognitions'])}\n"
        f"⬇️ MP3 downloads: {esc(s['total_downloads'])}\n"
        f"🚫 Banned: {esc(s['banned_users'])}\n"
        f"🗄 Cached tracks: {esc(cache_n)}\n\n"
        "*📅 Today*\n"
        f"🎧 Recognitions: {esc(t['recognitions'])}\n"
        f"⬇️ Downloads: {esc(t['downloads'])}\n"
        f"🆕 New users: {esc(t['new_users'])}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await db.get_history(update.effective_user.id, limit=10)
    if not rows:
        await update.message.reply_text("📭 No recognition history yet — send me a link to get started!")
        return
    lines = ["*🕘 Your Recent Recognitions*", ""]
    for r in rows:
        when = r["recognized_at"][:16].replace("T", " ")
        lines.append(f"🎵 {esc(r['title'])} — {esc(r['artist'])} \\({esc(when)} UTC\\)")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await db.leaderboard(10)
    if not rows:
        await update.message.reply_text("No data yet.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["*🏆 Leaderboard*", ""]
    for i, r in enumerate(rows):
        rank = medals[i] if i < 3 else f"{i + 1}\\."
        name = f"@{r['username']}" if r["username"] else f"User {r['user_id']}"
        lines.append(f"{rank} {esc(name)} — {esc(r['recognition_count'])} recognitions")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = await db.get_quality(update.effective_user.id)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(("✅ " if current == "best" else "") + "Best Quality", callback_data="quality:best"),
            ],
            [
                InlineKeyboardButton(("✅ " if current == "720p" else "") + "720p", callback_data="quality:720p"),
                InlineKeyboardButton(("✅ " if current == "480p" else "") + "480p", callback_data="quality:480p"),
            ],
        ]
    )
    await update.message.reply_text("⚙️ *Preferred video quality:*", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard)


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start = time.monotonic()
    msg = await update.message.reply_text("🏓 Pinging...")
    latency = (time.monotonic() - start) * 1000
    await safe_edit(msg, f"🏓 Pong! `{latency:.0f}ms`", parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "*🎵 SongID Bot*\n\n"
        "A premium song\\-recognition bot for social media links and direct uploads\\.\n"
        "Owner: @ankneewayz\n"
        "Built with python\\-telegram\\-bot, yt\\-dlp, Cobalt & a Shazam recognition API\\."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


# ---- Admin commands ----------------------------------------------------------


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not CFG.is_admin(update.effective_user.id):
            await update.message.reply_text("🚫 This command is admin\\-only\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        return await func(update, context)

    return wrapper


@admin_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /broadcast <text>
    /broadcast <text> | <button text> | <button url>
    Reply to a photo with /broadcast <caption> to broadcast that photo.
    """
    raw = update.message.text.partition(" ")[2].strip()
    if not raw and not (update.message.reply_to_message and update.message.reply_to_message.photo):
        await update.message.reply_text(
            "Usage: /broadcast <message>\n"
            "Or: /broadcast <message> | <button text> | <button url>\n"
            "Or reply to a photo with /broadcast <caption>"
        )
        return

    button_markup = None
    parts = [p.strip() for p in raw.split("|")]
    text = parts[0]
    if len(parts) == 3:
        button_markup = InlineKeyboardMarkup([[InlineKeyboardButton(parts[1], url=parts[2])]])

    photo_file_id = None
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        photo_file_id = update.message.reply_to_message.photo[-1].file_id

    user_ids = await db.all_user_ids()
    status = await update.message.reply_text(f"📢 Broadcasting to {len(user_ids)} users...")
    sent, failed = 0, 0
    for uid in user_ids:
        try:
            if photo_file_id:
                await context.bot.send_photo(uid, photo=photo_file_id, caption=text or None, reply_markup=button_markup)
            else:
                await context.bot.send_message(uid, f"📢 {text}", reply_markup=button_markup)
            sent += 1
        except TelegramError:
            failed += 1
        await asyncio.sleep(0.05)  # gentle flood-control pacing
    await safe_edit(status, f"✅ Broadcast complete. Sent: {sent}, Failed: {failed}")


@admin_only
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = await db.stats()
    await update.message.reply_text(f"👥 Total users: {s['total_users']}\n🚫 Banned: {s['banned_users']}")


@admin_only
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /ban <user_id>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user id.")
        return
    await db.set_banned(uid, True)
    await update.message.reply_text(f"🚫 User {uid} has been banned.")


@admin_only
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /unban <user_id>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user id.")
        return
    await db.set_banned(uid, False)
    await update.message.reply_text(f"✅ User {uid} has been unbanned.")


@admin_only
async def cmd_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0].lower() == "clear":
        n = await db.clear_cache()
        await update.message.reply_text(f"🗑 Cleared {n} cached entries.")
        return
    n = await db.cache_count()
    await update.message.reply_text(
        f"🗄 Cached entries: {n}\nUse `/cache clear` to wipe the cache.", parse_mode=ParseMode.MARKDOWN_V2
    )


@admin_only
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not LOG_PATH.exists():
        await update.message.reply_text("No logs yet.")
        return
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-40:]
        text = "".join(lines) or "Log file is empty."
        if len(text) > MAX_TG_MSG:
            with open(LOG_PATH, "rb") as f:
                await update.message.reply_document(f, filename="bot.log")
        else:
            await update.message.reply_text(f"```\n{text[-MAX_TG_MSG:]}\n```", parse_mode=ParseMode.MARKDOWN_V2)
    except OSError as e:
        await update.message.reply_text(f"Couldn't read logs: {e}")


@admin_only
async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("♻️ Restarting bot...")
    logger.info("Restart requested by admin %s", update.effective_user.id)
    await context.application.stop()
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ==============================================================================
# MESSAGE + CALLBACK HANDLERS
# ==============================================================================


async def _rate_limited(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> Optional[float]:
    if CFG.is_admin(user_id):
        return None
    last_map: Dict[int, float] = context.bot_data.setdefault("last_request", {})
    now = time.monotonic()
    last = last_map.get(user_id, 0)
    if now - last < CFG.rate_limit_seconds:
        return CFG.rate_limit_seconds - (now - last)
    last_map[user_id] = now
    return None


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user = update.effective_user
    await db.touch_user(user.id, user.username)

    if await db.is_banned(user.id):
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return

    if not await check_membership(context.bot, user.id):
        await update.message.reply_text(
            "🔒 Please join our channel to use this bot.", reply_markup=join_gate_keyboard()
        )
        return

    wait = await _rate_limited(user.id, context)
    if wait:
        await update.message.reply_text(f"⏳ Please wait {wait:.0f}s before your next request.")
        return

    url = extract_url(update.message.text)
    if not url:
        await update.message.reply_text(
            "🔗 Please send a valid link from a supported platform, or a video/audio/voice file.\nUse /help for details."
        )
        return

    await process_url(update, context, url)


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle direct video / audio / voice uploads (no URL involved)."""
    message = update.message
    user = update.effective_user
    await db.touch_user(user.id, user.username)

    if await db.is_banned(user.id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return

    if not await check_membership(context.bot, user.id):
        await message.reply_text("🔒 Please join our channel to use this bot.", reply_markup=join_gate_keyboard())
        return

    wait = await _rate_limited(user.id, context)
    if wait:
        await message.reply_text(f"⏳ Please wait {wait:.0f}s before your next request.")
        return

    if message.video:
        tg_file_obj, kind, suffix = message.video, "video", ".mp4"
    elif message.document and (message.document.mime_type or "").startswith("video"):
        tg_file_obj, kind, suffix = message.document, "video", ".mp4"
    elif message.audio:
        tg_file_obj, kind, suffix = message.audio, "audio", ".mp3"
    elif message.voice:
        tg_file_obj, kind, suffix = message.voice, "audio", ".ogg"
    else:
        return

    size = getattr(tg_file_obj, "file_size", None)
    if size and size > CFG.max_file_size_mb * 1024 * 1024:
        await message.reply_text(f"❌ File is too large ({size / 1e6:.0f}MB, limit {CFG.max_file_size_mb}MB).")
        return

    status = await message.reply_text(f"{PLATFORM_EMOJI['telegram']} Receiving your file...")
    work_dir = TMP_DIR / uuid.uuid4().hex[:10]
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        await safe_edit(status, "⬇ Downloading...")
        tg_file = await context.bot.get_file(tg_file_obj.file_id)
        dest = work_dir / f"input{suffix}"
        await tg_file.download_to_drive(str(dest))
    except TelegramError as e:
        await safe_edit(status, f"❌ Couldn't fetch the file from Telegram: {esc(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)
        cleanup_paths(work_dir)
        return

    await run_pipeline(
        update, context, status, dest, work_dir,
        source="telegram", media_kind=kind, url=None, force_refresh=False,
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return

    action, _, payload = query.data.partition(":")

    if action == "quality":
        await db.set_quality(update.effective_user.id, payload)
        await query.answer(f"✅ Quality set to {payload}")
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton(("✅ " if payload == "best" else "") + "Best Quality", callback_data="quality:best")],
                        [
                            InlineKeyboardButton(("✅ " if payload == "720p" else "") + "720p", callback_data="quality:720p"),
                            InlineKeyboardButton(("✅ " if payload == "480p" else "") + "480p", callback_data="quality:480p"),
                        ],
                    ]
                )
            )
        except TelegramError:
            pass
        return

    if action == "checkjoin":
        ok = await check_membership(context.bot, update.effective_user.id)
        if ok:
            await query.answer("✅ Access granted! Send your link again.", show_alert=True)
        else:
            await query.answer("❌ You haven't joined the channel yet.", show_alert=True)
        return

    session = await db.get_session(payload)
    if not session:
        await query.answer("This session has expired. Please resend the link or file.", show_alert=True)
        return

    if action == "mp3":
        await query.answer("Preparing MP3...")
        await send_mp3(update, context, session)

    elif action == "again":
        await query.answer("Recognizing again...")
        if session["url"]:
            await process_url(update, context, session["url"], force_refresh=True)
        elif session["local_path"] and Path(session["local_path"]).exists():
            work_dir = TMP_DIR / uuid.uuid4().hex[:10]
            work_dir.mkdir(parents=True, exist_ok=True)
            status = await context.bot.send_message(update.effective_chat.id, "🧠 Re-recognizing...")
            await run_pipeline(
                update, context, status, Path(session["local_path"]), work_dir,
                source=session["source"], media_kind=session["media_kind"], url=None, force_refresh=True,
            )
        else:
            await context.bot.send_message(update.effective_chat.id, "❌ The original media has expired.")

    elif action == "copy":
        meta = session["metadata"]
        song_name = f"{meta.get('title', 'Unknown')} - {meta.get('artist', 'Unknown')}"
        await query.answer(song_name[:190], show_alert=True)
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=f"`{esc(song_name)}`", parse_mode=ParseMode.MARKDOWN_V2
        )

    elif action == "lyrics":
        meta = session["metadata"]
        lyrics = meta.get("lyrics")
        await query.answer()
        if not lyrics:
            await context.bot.send_message(update.effective_chat.id, "📝 No lyrics available for this track.")
            return
        full_text = f"📝 *Lyrics — {esc(meta.get('title'))}*\n\n{esc(lyrics)}"
        for chunk in chunk_text_by_lines(full_text, MAX_TG_MSG):
            await context.bot.send_message(update.effective_chat.id, chunk, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await query.answer("Unknown action.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling update: %s", context.error)
    tb = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))
    logger.error(tb)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ An unexpected error occurred. The issue has been logged.")
        except TelegramError:
            pass


# ==============================================================================
# BACKGROUND JOBS
# ==============================================================================


async def cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        n = await db.purge_expired_sessions(CFG.session_ttl_minutes)
        if n:
            logger.info("Cleanup: purged %d expired session(s).", n)
    except Exception:
        logger.exception("Session cleanup job failed")

    # safety net: remove orphaned tmp/ work dirs older than 1 hour
    cutoff = time.time() - 3600
    try:
        for child in TMP_DIR.iterdir():
            try:
                if child.stat().st_mtime < cutoff:
                    cleanup_paths(child)
            except OSError:
                continue
    except OSError:
        pass


# ==============================================================================
# LIFECYCLE
# ==============================================================================


async def on_startup(application: Application):
    await db.connect()
    session = aiohttp.ClientSession()
    application.bot_data["http_session"] = session
    application.bot_data["downloader"] = MediaDownloader(session)
    application.bot_data["recognizer"] = Recognizer(session)
    application.bot_data["job_semaphore"] = asyncio.Semaphore(CFG.max_concurrent_jobs)
    application.bot_data["last_request"] = {}

    if application.job_queue:
        application.job_queue.run_repeating(cleanup_job, interval=900, first=60)
    else:
        logger.warning(
            "JobQueue unavailable — install with `pip install \"python-telegram-bot[job-queue]\"` "
            "to enable automatic session/cache cleanup."
        )

    logger.info("SongID Bot started successfully.")

    if not ffmpeg_available():
        logger.critical("FFmpeg/ffprobe not found on PATH — recognition and audio extraction will fail!")

    for admin_id in CFG.admin_ids:
        try:
            msg = "🤖 SongID Bot is now online."
            if not ffmpeg_available():
                msg += "\n⚠️ WARNING: ffmpeg/ffprobe not found — the bot cannot process media until this is fixed."
            await application.bot.send_message(admin_id, msg)
        except TelegramError:
            pass


async def on_shutdown(application: Application):
    session: Optional[aiohttp.ClientSession] = application.bot_data.get("http_session")
    if session:
        await session.close()
    await db.close()
    cleanup_paths(TMP_DIR)
    logger.info("SongID Bot shut down cleanly.")


def build_application() -> Application:
    problems = CFG.validate()
    if problems:
        for p in problems:
            logger.error("Config error: %s", p)
        raise SystemExit("Fix the .env file before starting the bot:\n" + "\n".join(problems))

    application = (
        ApplicationBuilder()
        .token(CFG.bot_token)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("history", cmd_history))
    application.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    application.add_handler(CommandHandler("settings", cmd_settings))
    application.add_handler(CommandHandler("ping", cmd_ping))
    application.add_handler(CommandHandler("about", cmd_about))

    application.add_handler(CommandHandler("broadcast", cmd_broadcast))
    application.add_handler(CommandHandler("users", cmd_users))
    application.add_handler(CommandHandler("ban", cmd_ban))
    application.add_handler(CommandHandler("unban", cmd_unban))
    application.add_handler(CommandHandler("cache", cmd_cache))
    application.add_handler(CommandHandler("logs", cmd_logs))
    application.add_handler(CommandHandler("restart", cmd_restart))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    application.add_handler(
        MessageHandler(filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.VIDEO, on_media)
    )
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_error_handler(on_error)

    return application


def main():
    app = build_application()
    logger.info("Starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)


if __name__ == "__main__":
    main()
