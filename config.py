import os

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

ACHIEVEMENT_ROLE_IDS = [
    1543500048245981275,
    1543500112456585267,
    1543500149798473828,
]

# --- 募集機能用設定 ---
CHANNEL_TMOD = 1543542274963804220
CHANNEL_CONSOLE = 1543542426558398524
CHANNEL_VANILLA = 1543542257351790682

ROLE_IDS = {
    "モッドマルチ": 1543544171069972500,  # ✋ | モッドマルチ希望 のロールID
    "バニラマルチ": 1543544231652491324,  # ✋ | バニラテラリアマルチ希望 のロールID
    "Switch": 1543544279627071539,       # 🖐️ | Switch のロールID
    "PS4": 1543544726412726392,          # 🕹️ | PS4 のロールID
    "XBOX": 1543544340205404211,         # ✖️ | XBOX のロールID
    "モバイル": 1543544406022426664,     # 📱 | モバイル のロールID
    "PC": 1543544445931229254,           # ⌨️ | PC のロールID
}

# --- VCチャンネルIDの定義 (プレフィックスと番号の組み合わせで管理) ---
# 例: "バニラVC1": 1543549627553546350
VC_CHANNEL_IDS = {
    # バニラVC (1〜5)
    "バニラVC1": 1543549627553546350,
    "バニラVC2": None,
    "バニラVC3": None,
    "バニラVC4": None,
    "バニラVC5": None,
    
    # tmodVC (1〜5)
    "tmodVC1": None,
    "tmodVC2": None,
    "tmodVC3": 1543549797003173909,
    "tmodVC4": None,
    "tmodVC5": None,
    
    # CSVC (1〜5) ※コンソール用
    "CSVC1": None,
    "CSVC2": None,
    "CSVC3": None,
    "CSVC4": None,
    "CSVC5": None,
}

# --- サイト（Terraria JP 公式サイト）との同期 ---
# サイト側は Discord を叩かない。Bot が集めた内容を、ここへ送って写してもらう。
# Render の Environment に WEB_SYNC_URL と SYNC_SECRET を入れると動き出す。
# SYNC_SECRET はサイト側（npx wrangler secret put SYNC_SECRET）と同じ文字列にすること。
WEB_SYNC_URL = os.getenv(
    "WEB_SYNC_URL",
    "https://terraria-jp.terraria-jp-website.workers.dev/api/internal/recruitments",
)
SYNC_SECRET = os.getenv("SYNC_SECRET")

# 何分ごとに送るか。短くしすぎても Discord を叩きすぎるだけなので、1 分未満にはしない。
try:
    SYNC_INTERVAL_MINUTES = max(1, int(os.getenv("SYNC_INTERVAL_MINUTES", "5")))
except ValueError:
    SYNC_INTERVAL_MINUTES = 5
