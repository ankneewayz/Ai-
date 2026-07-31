import os
import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# Initialize FastAPI App
app = FastAPI(title="d-tube OS Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Backend Scraper Endpoint using PyTubeSearch
@app.get("/api/search")
def search_videos(q: str = Query("Trending 2026", min_length=1), limit: int = 24):
    try:
        from pytubesearch import PyTubeSearch
        results = PyTubeSearch(q, limit=limit).get_videos()
        return {"status": "success", "data": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# Serve Combined Glassmorphic UI Frontend
@app.get("/", response_class=HTMLResponse)
def serve_ui():
    return HTML_CONTENT

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>d-tube OS — PyTube Engine</title>
    <style>
        :root {
            --bg-base: #030305;
            --surface-glass: rgba(22, 22, 28, 0.7);
            --surface-hover: rgba(255, 255, 255, 0.08);
            --surface-card: #0d0d12;
            --accent-glow: #ff2d55;
            --accent-gradient: linear-gradient(135deg, #ff2d55 0%, #ff3b30 100%);
            --text-primary: #ffffff;
            --text-secondary: rgba(255, 255, 255, 0.6);
            --border-glass: rgba(255, 255, 255, 0.1);
            --ios-spring: cubic-bezier(0.2, 0.8, 0.2, 1);
            --ios-bounce: cubic-bezier(0.34, 1.56, 0.64, 1);
        }

        * {
            margin: 0; padding: 0; box-sizing: border-box;
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", Roboto, sans-serif;
            -webkit-tap-highlight-color: transparent;
            user-select: none;
        }

        body {
            background-color: var(--bg-base);
            color: var(--text-primary);
            min-height: 100vh;
            overflow-x: hidden;
            overflow-y: scroll;
        }

        .ambient-bg {
            position: fixed; top: -20vh; left: 50%; transform: translateX(-50%);
            width: 100vw; height: 60vh;
            background: radial-gradient(circle, rgba(255, 45, 85, 0.14) 0%, rgba(0,0,0,0) 70%);
            pointer-events: none; z-index: 0; filter: blur(80px);
        }

        :focus-visible, .tv-focused {
            outline: none !important;
            border-color: var(--accent-glow) !important;
            box-shadow: 0 0 0 3px rgba(255, 45, 85, 0.4), 0 12px 30px rgba(0,0,0,0.8) !important;
            transform: scale(1.03) !important;
        }

        header {
            position: sticky; top: 12px; z-index: 1000;
            margin: 0 auto; width: calc(100% - 32px); max-width: 1400px;
            padding: 10px 20px;
            background: var(--surface-glass);
            backdrop-filter: blur(25px) saturate(190%);
            -webkit-backdrop-filter: blur(25px) saturate(190%);
            border: 1px solid var(--border-glass);
            border-radius: 28px;
            display: flex; align-items: center; justify-content: space-between; gap: 16px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.5);
        }

        .brand { display: flex; align-items: center; gap: 10px; cursor: pointer; text-decoration: none; }
        .logo-icon {
            width: 34px; height: 34px; background: var(--accent-gradient);
            border-radius: 10px; display: flex; align-items: center; justify-content: center;
            box-shadow: 0 4px 12px rgba(255, 45, 85, 0.4);
        }
        .logo-icon svg { width: 18px; height: 18px; fill: #fff; }
        .brand-text {
            font-size: 20px; font-weight: 800; letter-spacing: -0.5px;
            background: linear-gradient(180deg, #ffffff 0%, rgba(255,255,255,0.7) 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }

        .search-box { position: relative; flex: 1; max-width: 500px; display: flex; align-items: center; }
        .search-input {
            width: 100%; padding: 12px 46px 12px 18px;
            border-radius: 20px; border: 1px solid var(--border-glass);
            background: rgba(0, 0, 0, 0.5); color: var(--text-primary);
            font-size: 14px; outline: none; transition: all 0.3s var(--ios-spring);
        }
        .search-input:focus {
            background: rgba(0, 0, 0, 0.8); border-color: var(--accent-glow);
            box-shadow: 0 0 0 3px rgba(255, 45, 85, 0.2);
        }
        .search-btn {
            position: absolute; right: 6px; background: var(--surface-hover);
            border: none; width: 34px; height: 34px; border-radius: 14px;
            display: flex; align-items: center; justify-content: center; cursor: pointer;
        }
        .search-btn svg { width: 16px; height: 16px; fill: var(--text-secondary); }

        .categories-wrapper {
            max-width: 1400px; margin: 20px auto 0; padding: 0 16px;
            display: flex; gap: 10px; overflow-x: auto; scrollbar-width: none;
        }
        .categories-wrapper::-webkit-scrollbar { display: none; }
        .cat-chip {
            padding: 8px 18px; background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-glass); border-radius: 20px;
            font-size: 13px; font-weight: 600; color: var(--text-secondary);
            white-space: nowrap; cursor: pointer; transition: all 0.3s var(--ios-spring);
        }
        .cat-chip.active, .cat-chip:hover {
            background: var(--text-primary); color: #000; border-color: var(--text-primary);
        }

        main { max-width: 1400px; margin: 0 auto; padding: 24px 16px 100px; }
        .video-grid {
            display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 22px;
        }
        @media (min-width: 1200px) { .video-grid { grid-template-columns: repeat(4, 1fr); gap: 26px; } }
        @media (min-width: 1600px) { .video-grid { grid-template-columns: repeat(5, 1fr); } }

        .video-card {
            background: var(--surface-card); border: 1px solid var(--border-glass);
            border-radius: 22px; overflow: hidden; cursor: pointer;
            transition: transform 0.4s var(--ios-spring), border-color 0.3s ease;
            display: flex; flex-direction: column;
        }
        .video-card:hover {
            transform: translateY(-6px) scale(1.015); border-color: rgba(255, 255, 255, 0.25);
            box-shadow: 0 20px 40px -10px rgba(0, 0, 0, 0.7);
        }

        .thumbnail-container { position: relative; width: 100%; aspect-ratio: 16/9; background: #121217; }
        .thumbnail-img { width: 100%; height: 100%; object-fit: cover; }
        .badge-time {
            position: absolute; bottom: 8px; right: 8px;
            background: rgba(0, 0, 0, 0.8); backdrop-filter: blur(6px);
            color: #fff; font-size: 11px; font-weight: 700; padding: 3px 7px; border-radius: 6px;
        }

        .card-details { padding: 14px; display: flex; gap: 12px; flex: 1; }
        .avatar {
            width: 36px; height: 36px; border-radius: 50%; background: var(--surface-hover);
            flex-shrink: 0; border: 1px solid var(--border-glass);
            display: flex; align-items: center; justify-content: center;
            font-weight: 700; font-size: 13px; color: var(--accent-glow);
        }
        .info { display: flex; flex-direction: column; gap: 4px; overflow: hidden; }
        .title {
            font-size: 14px; font-weight: 600; line-height: 1.35; color: var(--text-primary);
            display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
        }
        .meta { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }

        .modal-backdrop {
            position: fixed; inset: 0; background: rgba(0, 0, 0, 0.9);
            backdrop-filter: blur(20px); z-index: 2000;
            display: flex; align-items: center; justify-content: center;
            opacity: 0; pointer-events: none; transition: opacity 0.35s var(--ios-spring);
            padding: 16px;
        }
        .modal-backdrop.active { opacity: 1; pointer-events: auto; }
        .modal-box {
            width: 100%; max-width: 1050px; background: #0d0d12;
            border: 1px solid var(--border-glass); border-radius: 28px; overflow: hidden;
            transform: scale(0.92); transition: transform 0.4s var(--ios-bounce);
        }
        .modal-backdrop.active .modal-box { transform: scale(1); }

        .player-frame-wrapper { position: relative; width: 100%; aspect-ratio: 16/9; background: #000; }
        .player-frame-wrapper iframe { width: 100%; height: 100%; border: none; }

        .modal-bar {
            padding: 16px 20px; display: flex; align-items: center;
            justify-content: space-between; gap: 16px; background: rgba(255, 255, 255, 0.02);
        }
        .modal-title { font-size: 15px; font-weight: 700; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

        .btn-dl {
            background: var(--accent-gradient); color: #fff; border: none;
            padding: 8px 16px; border-radius: 14px; font-size: 13px; font-weight: 700;
            cursor: pointer; text-decoration: none; display: flex; align-items: center; gap: 6px;
        }
        .btn-close {
            background: rgba(255, 255, 255, 0.1); border: 1px solid var(--border-glass);
            color: #fff; width: 34px; height: 34px; border-radius: 50%; cursor: pointer;
        }

        .spinner-box { display: flex; justify-content: center; padding: 60px 0; grid-column: 1 / -1; }
        .spinner {
            width: 36px; height: 36px; border: 3px solid var(--border-glass);
            border-top-color: var(--accent-glow); border-radius: 50%;
            animation: spin 0.7s cubic-bezier(0.6, 0.2, 0.4, 0.8) infinite;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    </style>
</head>
<body>

    <div class="ambient-bg"></div>

    <header>
        <a class="brand" onclick="loadQuery('Trending 2026')" tabindex="0">
            <div class="logo-icon">
                <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
            </div>
            <span class="brand-text">d-tube OS</span>
        </a>

        <div class="search-box">
            <input type="text" id="searchInput" class="search-input" placeholder="Search videos direct..." autocomplete="off" tabindex="0">
            <button id="searchBtn" class="search-btn" tabindex="0">
                <svg viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
            </button>
        </div>
    </header>

    <div class="categories-wrapper" id="categoriesBar">
        <div class="cat-chip active" onclick="selectCat(this, 'Trending 2026')" tabindex="0">🔥 Trending</div>
        <div class="cat-chip" onclick="selectCat(this, 'Music Hits')" tabindex="0">🎵 Music</div>
        <div class="cat-chip" onclick="selectCat(this, 'Gaming Live')" tabindex="0">🎮 Gaming</div>
        <div class="cat-chip" onclick="selectCat(this, 'Tech Reviews')" tabindex="0">📱 Tech</div>
        <div class="cat-chip" onclick="selectCat(this, 'Cybersecurity')" tabindex="0">🛡️ Cyber</div>
        <div class="cat-chip" onclick="selectCat(this, '4K Nature 60fps')" tabindex="0">🌿 4K Relaxation</div>
    </div>

    <main>
        <div id="videoGrid" class="video-grid"></div>
        <div id="spinnerBox" class="spinner-box"><div class="spinner"></div></div>
    </main>

    <div id="playerModal" class="modal-backdrop">
        <div class="modal-box">
            <div class="player-frame-wrapper">
                <iframe id="playerIframe" src="" allow="autoplay; encrypted-media; fullscreen" allowfullscreen></iframe>
            </div>
            <div class="modal-bar">
                <div id="modalTitle" class="modal-title">Playing Video</div>
                <div style="display:flex; gap:10px; align-items:center;">
                    <a id="dlBtn" href="https://cobalt.tools" target="_blank" class="btn-dl" tabindex="0">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="#fff"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
                        Download
                    </a>
                    <button class="btn-close" onclick="closePlayer()" tabindex="0">✕</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        const videoGrid = document.getElementById('videoGrid');
        const spinnerBox = document.getElementById('spinnerBox');
        const searchInput = document.getElementById('searchInput');
        const searchBtn = document.getElementById('searchBtn');
        const playerModal = document.getElementById('playerModal');
        const playerIframe = document.getElementById('playerIframe');
        const modalTitle = document.getElementById('modalTitle');

        async function loadQuery(q) {
            spinnerBox.style.display = 'flex';
            videoGrid.innerHTML = '';
            
            try {
                const response = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
                const json = await response.json();
                
                if (json.status === 'success' && json.data) {
                    renderCards(json.data);
                } else {
                    videoGrid.innerHTML = `<div style="grid-column:1/-1; text-align:center; padding:40px;">No results found.</div>`;
                }
            } catch (err) {
                console.error("Scraper Backend Error:", err);
                videoGrid.innerHTML = `<div style="grid-column:1/-1; text-align:center; color:#ff2d55; padding:40px;">Backend connection error.</div>`;
            } finally {
                spinnerBox.style.display = 'none';
            }
        }

        function renderCards(videos) {
            videos.forEach(item => {
                const id = item.id || item.videoId;
                if (!id) return;

                const title = item.title || 'Untitled';
                const channel = item.channel?.name || item.author || 'Unknown Channel';
                const duration = item.duration || item.duration_string || '';
                const thumb = item.thumbnails?.[0]?.url || item.thumbnail || `https://i.ytimg.com/vi/${id}/hqdefault.jpg`;

                const card = document.createElement('div');
                card.className = 'video-card';
                card.setAttribute('tabindex', '0');

                card.innerHTML = `
                    <div class="thumbnail-container" onclick="playVideo('${id}', '${escapeQuotes(title)}')">
                        <img class="thumbnail-img" src="${thumb}" loading="lazy" alt="${escapeQuotes(title)}">
                        ${duration ? `<span class="badge-time">${duration}</span>` : ''}
                    </div>
                    <div class="card-details" onclick="playVideo('${id}', '${escapeQuotes(title)}')">
                        <div class="avatar">${channel.charAt(0).toUpperCase()}</div>
                        <div class="info">
                            <div class="title">${escapeHtml(title)}</div>
                            <div class="meta">${escapeHtml(channel)}</div>
                        </div>
                    </div>
                `;

                card.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') playVideo(id, title);
                });

                videoGrid.appendChild(card);
            });
        }

        function playVideo(id, title) {
            modalTitle.innerText = title;
            playerIframe.src = `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&modestbranding=1&rel=0`;
            playerModal.classList.add('active');
        }

        function closePlayer() {
            playerModal.classList.remove('active');
            playerIframe.src = '';
        }

        function selectCat(el, q) {
            document.querySelectorAll('.cat-chip').forEach(c => c.classList.remove('active'));
            el.classList.add('active');
            loadQuery(q);
        }

        searchBtn.addEventListener('click', () => {
            const q = searchInput.value.trim();
            if (q) loadQuery(q);
        });

        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                const q = searchInput.value.trim();
                if (q) loadQuery(q);
            }
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closePlayer();
        });

        function escapeHtml(str) {
            return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }

        function escapeQuotes(str) {
            return str.replace(/'/g, "\\'").replace(/"/g, '&quot;');
        }

        loadQuery('Trending 2026');
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    # Dynamically bind to Render's allocated port variable
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
