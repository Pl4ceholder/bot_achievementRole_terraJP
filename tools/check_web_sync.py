"""サイトへ送る中身を、Discord に繋がずに確かめる。

    python tools/check_web_sync.py            # 送る中身を表示するだけ
    python tools/check_web_sync.py --post     # サイトへ実際に送る

送る中身（募集・イベント）を表示したうえで、最後に次の規則も確かめる。合っていなければ
終了コード 1 で終わる。

- 片方の同期が失敗しても、もう片方は行われるか
- ID から最後の書き込みの時刻を取り出せるか
- 募集が流れる時刻を求められるか（Discord の一覧と、サイトの表示に使う）
- 流れるまでの残りを文にできるか
- Discord の時刻表示（`<t:...>`）に使う秒を取り出せるか
- 実績を、送る形にできるか（消えたロールを送らない・並べ方が決まっている）
- 実績を送る間隔を守れるか（サイト側の書き込みを無駄に使わない）

このファイルは discord も aiohttp も使わない（標準ライブラリだけ）。Bot のトークンが
無くても動くので、送る形が合っているかを手元で確かめられる。

--post は環境変数 WEB_SYNC_BASE / SYNC_SECRET を使う。ローカルのサイトへ送るときは:

    set WEB_SYNC_BASE=http://127.0.0.1:8787
    set SYNC_SECRET=（.dev.vars と同じ文字列）
    python tools/check_web_sync.py --post
"""

import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (  # noqa: E402
    ACHIEVEMENT_ROLE_IDS,
    CHANNEL_CONSOLE,
    CHANNEL_TMOD,
    CHANNEL_VANILLA,
    WEB_SYNC_BASE,
)
from web_sync import (  # noqa: E402
    DISCORD_EPOCH_MS,
    achievement_master,
    achievement_members,
    build_achievements_payload,
    build_events_payload,
    build_payload,
    collect_events,
    collect_recruitments,
    deadline_of,
    is_achievements_due,
    remaining_text,
    run_each,
    snowflake_time,
    to_unix,
)

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


class FakeChannel:
    """チャンネル。募集の元メッセージはここ（親チャンネル）に置く。

    本物と同じで、元メッセージはスレッドからは取れない（スレッド側から取ると 404 に
    なる）。ここを取り違えると本番でだけ落ちるので、ダミーも同じ形にしている。
    """

    def __init__(self, channel_id=0, name=""):
        self.id = channel_id
        self.name = name
        self.message = None

    async def fetch_message(self, message_id):
        if self.message is None or self.message.id != message_id:
            raise LookupError("Unknown Message")  # 本物では 404 になるところ
        return self.message


class FakeThread:
    """`fetch_message` はあえて持たせない（スレッドから元メッセージは取れない）。"""

    def __init__(
        self,
        thread_id,
        parent,
        created_at,
        last_message_id=None,
        message_count=0,
        auto_archive_duration=1440,
    ):
        self.id = thread_id
        self.parent = parent
        self.parent_id = parent.id
        self.created_at = created_at
        self.last_message_id = last_message_id
        self.message_count = message_count
        self.auto_archive_duration = auto_archive_duration
        self.name = ""  # 本物と同じく、スレッドには名前がある（make_thread が入れる）


class FakeStatus:
    def __init__(self, name):
        self.name = name


class FakeEvent:
    def __init__(
        self,
        *,
        event_id,
        name,
        start_time,
        description="",
        end_time=None,
        location=None,
        channel=None,
        url="",
        user_count=0,
        status="scheduled",
    ):
        self.id = event_id
        self.name = name
        self.description = description
        self.start_time = start_time
        self.end_time = end_time
        self.location = location
        self.channel = channel
        self.cover_image = None
        self.url = url
        self.user_count = user_count
        self.status = FakeStatus(status)


class FakeGuild:
    """`active_threads` は本物も await が要る（非同期）。ここを同期にすると、
    本番でだけ落ちる間違いを素通りさせてしまうので、形は必ず本物に合わせる。
    """

    def __init__(self, guild_id, threads, channels, events):
        self.id = guild_id
        self._threads = threads
        self._channels = channels
        self._events = events

    async def active_threads(self):
        return list(self._threads)

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)

    async def fetch_scheduled_events(self, with_counts=True):
        return list(self._events)


def snowflake_for(moment: datetime) -> int:
    """その時刻を表す Discord の ID を作る（本物と同じ計算）。"""
    return (int(moment.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22


def make_thread(
    *, thread_id, channel, created_at, name, fields, last_activity_at=None, message_count=6
):
    """募集のスレッドを作る。元メッセージは親チャンネルに置く（本物と同じ）。"""
    author = FakeUser(user_id=thread_id + 1000, display_name=name)
    embed = FakeEmbed(fields, FakeAuthor(name=name, icon_url="https://example.com/i.png"))
    channel.message = FakeMessage(message_id=thread_id, embed=embed, author=author)
    thread = FakeThread(
        thread_id,
        channel,
        created_at,
        last_message_id=snowflake_for(last_activity_at or created_at),
        message_count=message_count,
    )
    # 本物のスレッドには名前がある（cogs/recruitment.py の thread_name と同じ形）。
    # 管理コマンドの候補はこの名前で選ぶので、ダミーにも持たせる。
    mode = fields[0][1] if fields else ""
    platform = fields[1][1] if len(fields) > 1 else ""
    thread.name = f"【{mode}/{platform}】{name}の募集" if platform else f"{name}のスレッド"
    return thread


def sample_guild() -> FakeGuild:
    """実際の embed（cogs/recruitment.py）と同じ見出しで作る。"""
    vanilla_channel = FakeChannel(CHANNEL_VANILLA, "バニラマルチ")
    tmod_channel = FakeChannel(CHANNEL_TMOD, "tmodマルチ")
    decoy_channel = FakeChannel(9999999999999999999, "雑談")

    vanilla = make_thread(
        thread_id=1111222233334444551,
        channel=vanilla_channel,
        created_at=datetime(2026, 10, 19, 3, 0, tzinfo=timezone.utc),
        last_activity_at=datetime(2026, 10, 19, 3, 20, tzinfo=timezone.utc),
        message_count=6,
        name="テスト太郎",
        fields=[
            ("🎮 種類", "バニラマルチ"),
            ("💻 機種", "PC"),
            ("🔊 VC", "<#1543549627553546350>"),  # <#...> はチャンネル名に直す
            ("🔗 SteamID / IP:Port", "123456789"),
            ("📝 メッセージ", "本日21:00〜 初心者歓迎！\n難易度エキスパートで遊びましょう"),
        ],
    )
    tmod = make_thread(
        thread_id=1111222233334444552,
        channel=tmod_channel,
        created_at=datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc),
        last_activity_at=datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc),
        message_count=12,
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
        channel=decoy_channel,
        created_at=datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc),
        name="雑談",
        fields=[("🎮 種類", "これは送られないはず")],
    )

    events = [
        # 外部のイベント。場所は文字で入っている。
        FakeEvent(
            event_id=2222333344445555666,
            name="第3回 建築コンテスト",
            description="テーマは「雪の城」。\n締め切りは Discord で案内します。",
            start_time=datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 10, 25, 14, 0, tzinfo=timezone.utc),
            location="Discord の「イベント」を開いてね",
            url="https://discord.com/events/999000111222333444/2222333344445555666",
            user_count=7,
        ),
        # ボイスのイベント。場所はチャンネル名を使う。終わりの時刻は無い。
        FakeEvent(
            event_id=2222333344445555667,
            name="まったり雑談",
            start_time=datetime(2026, 10, 20, 11, 0, tzinfo=timezone.utc),
            channel=FakeChannel(1543549627553546350, "バニラVC1"),
        ),
        # 終わったイベント。送ってはいけない。
        FakeEvent(
            event_id=2222333344445555668,
            name="終わったイベント",
            start_time=datetime(2026, 10, 1, 11, 0, tzinfo=timezone.utc),
            status="completed",
        ),
    ]

    channels = {
        CHANNEL_VANILLA: vanilla_channel,
        CHANNEL_TMOD: tmod_channel,
        1543549627553546350: FakeChannel(1543549627553546350, "バニラVC1"),  # VC 名の解決用
    }
    return FakeGuild(
        guild_id=999000111222333444,
        threads=[decoy, vanilla, tmod],
        channels=channels,
        events=events,
    )


def post(path: str, payload: dict) -> None:
    """サイトへ送る。"""
    secret = os.getenv("SYNC_SECRET")
    if not secret:
        print("SYNC_SECRET を環境変数に入れてから --post してください。")
        return

    url = f"{WEB_SYNC_BASE}{path}"
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


async def check_run_each() -> bool:
    """片方の同期が失敗しても、もう片方は行われるか。

    募集を読めない状態が続くと、イベントまでずっと古いままになってしまう。それを
    防ぐための規則なので、ここで確かめる。
    """
    done = []
    errors = []

    async def fail():
        raise RuntimeError("わざと失敗")

    async def succeed():
        done.append("イベント")

    try:
        await run_each(
            (("募集", fail), ("イベント", succeed)),
            lambda label, error: errors.append(f"{label}: {error}"),
        )
    except Exception as error:  # 規則が壊れていると、ここまで例外が上がってくる
        errors.append(f"例外がそのまま出た: {error}")

    return done == ["イベント"] and errors == ["募集: わざと失敗"]


def check_snowflake() -> bool:
    """Discord の ID から、その時刻を取り出せるか（最後の書き込みの表示に使う）。"""
    moment = datetime(2026, 10, 19, 3, 20, tzinfo=timezone.utc)
    return snowflake_time(snowflake_for(moment)) == moment.isoformat().replace("+00:00", "Z")


def check_deadline() -> bool:
    """募集が流れる時刻の求め方（サイト側 worker/recruitments.js と同じ考え方か）。

    書き込みが続いた募集は、立てた時刻ではなく「最後の書き込み」から数える。材料が
    無いときは、立てた時刻から既定の 24 時間後にしておく（判断できないものは消さない）。
    """
    bumped = deadline_of(
        {
            "startedAt": "2026-10-18T12:00:00Z",
            "lastActivityAt": "2026-10-19T10:00:00Z",
            "archiveMinutes": 1440,
        }
    )
    unknown = deadline_of(
        {
            "startedAt": "2026-10-18T12:00:00Z",
            "lastActivityAt": "",
            "archiveMinutes": 0,
        }
    )

    return bumped == "2026-10-20T10:00:00Z" and unknown == "2026-10-19T12:00:00Z"


def check_remaining() -> bool:
    """流れるまでの残りの言い方（Discord の一覧 `/recruit_list` に出る文）。"""
    now = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)

    return (
        remaining_text("2026-10-20T10:00:00Z", now) == "あと 22 時間で流れます"
        and remaining_text("2026-10-19T12:30:00Z", now) == "あと 30 分で流れます"
        and remaining_text("2026-10-19T11:30:00Z", now) == "流れる時刻を過ぎました"
        and remaining_text("", now) == ""
    )


def check_unix() -> bool:
    """Discord の時刻表示（`<t:...>`）に使う秒を取り出せるか。"""
    moment = datetime(2026, 10, 19, 3, 20, tzinfo=timezone.utc)

    return to_unix("2026-10-19T03:20:00Z") == int(moment.timestamp()) and to_unix("") == 0


def check_achievements() -> bool:
    """実績を、サイトへ送る形にできるか。

    名前が引けないロール（消されたもの）は送らない。並べ方は決めておく（同じ内容なら
    送る形も同じになるので、変わったかどうかが分かる）。
    """
    known = {10: "A", 20: "B", 30: "C"}

    master = achievement_master([10, 20, 30, 40], known)
    members = achievement_members(
        {200: [30, 10], 100: [10], 300: []},
        {200: 30, 100: None},
    )

    return master == [
        {"id": "10", "name": "A"},
        {"id": "20", "name": "B"},
        {"id": "30", "name": "C"},
    ] and members == [
        {"userId": "100", "unlocked": ["10"], "title": ""},
        {"userId": "200", "unlocked": ["10", "30"], "title": "30"},
        {"userId": "300", "unlocked": [], "title": ""},
    ]


def check_achievements_due() -> bool:
    """実績を送る間隔（送りすぎで、サイト側の書き込みを無駄に使わないか）。"""
    now = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)

    return (
        is_achievements_due(None, now, 30)  # 一度も送っていない
        and not is_achievements_due(now - timedelta(minutes=29), now, 30)
        and is_achievements_due(now - timedelta(minutes=30), now, 30)
    )


async def main() -> None:
    guild = sample_guild()
    channel_ids = {CHANNEL_TMOD, CHANNEL_CONSOLE, CHANNEL_VANILLA}

    recruitments = await collect_recruitments(guild, channel_ids)
    recruit_payload = build_payload(guild.id, recruitments)
    threads = await guild.active_threads()
    print(f"スレッド {len(threads)} 本のうち、募集 {len(recruitments)} 件を送ります。")
    print(json.dumps(recruit_payload, ensure_ascii=False, indent=2))

    events = await collect_events(guild)
    event_payload = build_events_payload(guild.id, events)
    print()
    print(f"イベント {len(guild._events)} 件のうち、これから {len(events)} 件を送ります。")
    print(json.dumps(event_payload, ensure_ascii=False, indent=2))

    # 実績は、集めるところが Discord と Redis に触る（cogs/sync.py の sync_achievements）。
    # ここでは、送る形が合っているかを見るための見本を作る。
    role_names = {role_id: f"実績{index + 1}" for index, role_id in enumerate(ACHIEVEMENT_ROLE_IDS)}
    achievements = achievement_master(ACHIEVEMENT_ROLE_IDS, role_names)
    members = achievement_members(
        {
            1000000000000000001: [ACHIEVEMENT_ROLE_IDS[0], ACHIEVEMENT_ROLE_IDS[2]],
            1000000000000000002: [ACHIEVEMENT_ROLE_IDS[1]],
        },
        {1000000000000000001: ACHIEVEMENT_ROLE_IDS[2]},
    )
    achievement_payload = build_achievements_payload(guild.id, achievements, members)
    print()
    print(f"実績 {len(achievements)} 件 / {len(members)} 人を送ります。")
    print(json.dumps(achievement_payload, ensure_ascii=False, indent=2))

    if "--post" in sys.argv:
        print()
        post("/api/internal/recruitments", recruit_payload)
        post("/api/internal/events", event_payload)
        post("/api/internal/achievements", achievement_payload)

    print()
    checks = (
        ("片方の同期が失敗しても、もう片方は行われる", await check_run_each()),
        ("ID から最後の書き込みの時刻を取り出せる", check_snowflake()),
        ("募集が流れる時刻を求められる（書き込みで延びる）", check_deadline()),
        ("流れるまでの残りを文にできる", check_remaining()),
        ("Discord の時刻表示に使う秒を取り出せる", check_unix()),
        ("実績を、送る形にできる", check_achievements()),
        ("実績を送る間隔を守れる", check_achievements_due()),
    )
    for title, ok in checks:
        print(("OK   " if ok else "ダメ  ") + title)

    if not all(ok for _, ok in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
