"""サイトへ送る中身を、Discord に繋がずに確かめる。

    python tools/check_web_sync.py            # 送る中身を表示するだけ
    python tools/check_web_sync.py --post     # サイトへ実際に送る

このファイルは discord も aiohttp も使わない（標準ライブラリだけ）。Bot のトークンが
無くても動くので、送る形が合っているかを手元で確かめられる。

--post は環境変数 WEB_SYNC_URL / SYNC_SECRET を使う。ローカルのサイトへ送るときは:

    set WEB_SYNC_URL=http://127.0.0.1:8787/api/internal/recruitments
    set SYNC_SECRET=（.dev.vars と同じ文字列）
    python tools/check_web_sync.py --post
"""

import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CHANNEL_CONSOLE, CHANNEL_TMOD, CHANNEL_VANILLA  # noqa: E402
from web_sync import build_payload, collect_recruitments  # noqa: E402

# 確認用のダミー。Discord のオブジェクトと同じ形（使う属性だけ）にしている。


class FakeField:
    def __init__(self, name, value):
        self.name = name
        self.value = value


class FakeAuthor:
    def __init__(self, name, icon_url):
        self.name = name
        self.icon_url = icon_url


class FakeEmbed:
    def __init__(self, fields, author):
        self.fields = [FakeField(name, value) for name, value in fields]
        self.author = author


class FakeUser:
    def __init__(self, user_id, display_name):
        self.id = user_id
        self.display_name = display_name


class FakeMessage:
    def __init__(self, message_id, embed, author):
        self.id = message_id
        self.embeds = [embed]
        self.author = author


class FakeThread:
    def __init__(self, thread_id, parent_id, created_at, message):
        self.id = thread_id
        self.parent_id = parent_id
        self.created_at = created_at
        self._message = message

    async def fetch_message(self, message_id):
        return self._message


class FakeChannel:
    def __init__(self, name):
        self.name = name


class FakeGuild:
    def __init__(self, guild_id, threads, channels):
        self.id = guild_id
        self._threads = threads
        self._channels = channels

    def active_threads(self):
        return list(self._threads)

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)


def make_thread(*, thread_id, channel_id, created_at, name, fields):
    author = FakeUser(user_id=thread_id + 1000, display_name=name)
    embed = FakeEmbed(fields, FakeAuthor(name=name, icon_url="https://example.com/i.png"))
    message = FakeMessage(message_id=thread_id, embed=embed, author=author)
    return FakeThread(thread_id, channel_id, created_at, message)


def sample_guild() -> FakeGuild:
    """実際の embed（cogs/recruitment.py）と同じ見出しで作る。"""
    vanilla = make_thread(
        thread_id=1111222233334444551,
        channel_id=CHANNEL_VANILLA,
        created_at=datetime(2026, 10, 19, 3, 0, tzinfo=timezone.utc),
        name="テスト太郎",
        fields=[
            ("🎮 種類", "バニラマルチ"),
            ("💻 機種", "PC"),
            ("🔊 VC", f"<#{1543549627553546350}>"),  # <#...> はチャンネル名に直す
            ("🔗 SteamID / IP:Port", "123456789"),
            ("📝 メッセージ", "本日21:00〜 初心者歓迎！\n難易度エキスパートで遊びましょう"),
        ],
    )
    tmod = make_thread(
        thread_id=1111222233334444552,
        channel_id=CHANNEL_TMOD,
        created_at=datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc),
        name="テスト花子",
        fields=[
            ("🎮 種類", "モッドマルチ"),
            ("💻 機種", "Switch"),
            ("🔊 VC", "tmodVC3"),
            ("🔗 SteamID / IP:Port", "なし"),
            ("📝 メッセージ", "カラミティをゆっくり進めます。途中参加OK"),
        ],
    )
    # 募集のチャンネルではないところに立ったスレッド。送ってはいけない。
    decoy = make_thread(
        thread_id=1111222233334444553,
        channel_id=9999999999999999999,
        created_at=datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc),
        name="雑談",
        fields=[("🎮 種類", "これは送られないはず")],
    )

    channels = {1543549627553546350: FakeChannel("バニラVC1")}
    return FakeGuild(guild_id=999000111222333444, threads=[decoy, vanilla, tmod], channels=channels)


def post(payload: dict) -> None:
    """サイトへ送る。"""
    url = os.getenv("WEB_SYNC_URL")
    secret = os.getenv("SYNC_SECRET")

    if not url or not secret:
        print("WEB_SYNC_URL と SYNC_SECRET を環境変数に入れてから --post してください。")
        return

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"content-type": "application/json", "x-sync-secret": secret},
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        print(f"送りました: HTTP {response.status} {url}")
        print(response.read().decode("utf-8"))


async def main() -> None:
    guild = sample_guild()
    channel_ids = {CHANNEL_TMOD, CHANNEL_CONSOLE, CHANNEL_VANILLA}

    items = await collect_recruitments(guild, channel_ids)
    payload = build_payload(guild.id, items)

    print(f"スレッド {len(guild.active_threads())} 本のうち、募集 {len(items)} 件を送ります。")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if "--post" in sys.argv:
        post(payload)


if __name__ == "__main__":
    asyncio.run(main())
