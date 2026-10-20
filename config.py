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

# 募集が立つチャンネル。サイトへの同期（cogs/sync.py）と、Discord 上の一覧
# （cogs/recruitment.py）で同じものを使う（片方だけ直して食い違うのを防ぐ）
RECRUIT_CHANNELS = {CHANNEL_TMOD, CHANNEL_CONSOLE, CHANNEL_VANILLA}

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
# Render の Environment に SYNC_SECRET を入れると動き出す。
# SYNC_SECRET はサイト側（npx wrangler secret put SYNC_SECRET）と同じ文字列にすること。
# 送り先は WEB_SYNC_BASE の下（/api/internal/...）。ふつうは変えなくてよい。
WEB_SYNC_BASE = os.getenv(
    "WEB_SYNC_BASE",
    "https://terraria-jp.terraria-jp-website.workers.dev",
)
SYNC_SECRET = os.getenv("SYNC_SECRET")

# 何分ごとに送るか。短くしすぎても Discord を叩きすぎるだけなので、1 分未満にはしない。
try:
    SYNC_INTERVAL_MINUTES = max(1, int(os.getenv("SYNC_INTERVAL_MINUTES", "5")))
except ValueError:
    SYNC_INTERVAL_MINUTES = 5

# 実績を送る間隔（分）。実績の付与は手動でたまにしか起きないので、募集やイベントより
# 長くする。5 分ごとに送ると、サイト側（D1）の書き込みを無駄に使うため。
try:
    ACHIEVEMENT_SYNC_MINUTES = max(1, int(os.getenv("ACHIEVEMENT_SYNC_MINUTES", "30")))
except ValueError:
    ACHIEVEMENT_SYNC_MINUTES = 30

# サイトからの依頼（実績の付与・剥奪、称号の着脱）を見に行く間隔（秒）。
# 短いほど反映が速いが、そのぶんサイトへのアクセスが増える。サイト側は 1 回あたり
# D1 を 1 回読むだけなので、30 秒でも無料枠には十分収まる（1 日 2,880 回）。
try:
    REQUEST_POLL_SECONDS = max(5, int(os.getenv("REQUEST_POLL_SECONDS", "30")))
except ValueError:
    REQUEST_POLL_SECONDS = 30
