"""サイト（Terraria JP 公式サイト）へ送る中身と、募集を Discord で見せるための計算。

このファイルは discord を import しない。そのため Bot のトークンが無くても、
`tools/check_web_sync.py` で中身を組み立てて確かめられる。Discord 上の一覧
（cogs/recruitment.py の `/recruit_list`）も、ここで作った値をそのまま並べるだけにして、
「いつ流れるか」の数え方が 2 か所に分かれないようにしている。

いま送っているのは 3 つ。

- マルチ募集…… Discord では「メッセージ＋スレッド」で、最後の書き込みから 24 時間で
  自動的に流れる。サイトはその写しを持ち、いま立っているものだけを出す。まだ生きている
  募集か分かるように、最後の書き込みの時刻と、書き込みの数も送る。
- イベント…… Discord の「イベント」（サーバーの予定）。終わったものは
  Discord が返さなくなるので、終了の同期はしない。
- 実績…… Discord の「実績ロール」。解放済みは Discord のロールには出ない（ロールは、
  いまつけている称号 1 つだけ）ので、サイトのマイページには bot から写すしかない。
  付与は手動でたまにしか起きないので、送る間隔は長めにする（cogs/sync.py）。
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Discord の ID に埋まっている時刻の基準（2015-01-01T00:00:00Z）
DISCORD_EPOCH_MS = 1420070400000

# Discord の既定。最後の書き込みから 24 時間で流れる
# （サイト側 worker/recruitments.js の DEFAULT_ARCHIVE_MINUTES と同じ）
DEFAULT_ARCHIVE_MINUTES = 1440

# 募集の embed の見出し（cogs/recruitment.py）を探すためのキーワード。
# 絵文字や細かい文言が変わっても壊れないよう、絵文字を外した語で探す。
KEY_MODE = "種類"
KEY_PLATFORM = "機種"
KEY_VC = "VC"
KEY_MESSAGE = "メッセージ"


def find_field(fields: list[tuple[str, str]], keyword: str) -> str:
    """embed の見出しをキーワードで探して、中身を返す。無ければ空文字。"""
    for name, value in fields:
        if keyword in name:
            return value
    return ""


def to_iso(value) -> str:
    """Discord の時刻を ISO 8601（UTC）の文字列にする。読めなければ空文字。"""
    try:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, ValueError):
        return ""


def to_int(value) -> int:
    """数を取り出す（負や読めないものは 0）。"""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def snowflake_time(snowflake) -> str:
    """Discord の ID から、それが作られた時刻を取り出す（ISO 8601、UTC）。

    ID の上位ビットが「2015-01-01 からのミリ秒」になっている。スレッドの最後の書き込みが
    いつだったかを知るために使う。discord を import せずに計算できる。
    """
    value = to_int(snowflake)
    if value <= 0:
        return ""

    moment = datetime.fromtimestamp(
        ((value >> 22) + DISCORD_EPOCH_MS) / 1000, tz=timezone.utc
    )
    return moment.isoformat().replace("+00:00", "Z")


def parse_iso(value):
    """ISO 8601 の文字列を時刻にする。読めなければ None。"""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def to_unix(value) -> int:
    """Discord の時刻表示（`<t:...>`）に使う秒。読めなければ 0。"""
    moment = parse_iso(value)
    return int(moment.timestamp()) if moment is not None else 0


def deadline_of(item: dict) -> str:
    """その募集が Discord から流れる時刻（ISO 8601、UTC）。

    最後の書き込みの時刻から、Discord の設定（分）だけ後。分からないときは、立てた時刻から
    既定の 24 時間後にしておく。サイト側（worker/recruitments.js）と同じ考え方。
    """
    base = parse_iso(item.get("lastActivityAt") or item.get("startedAt") or "")
    if base is None:
        return ""

    minutes = to_int(item.get("archiveMinutes")) or DEFAULT_ARCHIVE_MINUTES
    return (base + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def remaining_text(deadline_iso: str, now: datetime) -> str:
    """募集が流れるまでの残りを、短い文にする。読めなければ空文字。"""
    deadline = parse_iso(deadline_iso)
    if deadline is None:
        return ""

    left = deadline - now
    if left.total_seconds() <= 0:
        return "流れる時刻を過ぎました"

    minutes = int(left.total_seconds() // 60)
    if minutes < 60:
        return f"あと {minutes} 分で流れます"

    hours = round(minutes / 60)
    if hours < 24:
        return f"あと {hours} 時間で流れます"

    return f"あと {round(hours / 24)} 日で流れます"


def vc_name(guild, value: str) -> str:
    """VC は `<#123...>` の形で入っていることがある。名前が引ければ名前にする。"""
    if value.startswith("<#") and value.endswith(">"):
        try:
            channel = guild.get_channel(int(value[2:-1]))
        except (TypeError, ValueError):
            channel = None
        if channel is not None:
            return channel.name
    return value


def build_item(*, thread, message, guild, created_at) -> dict:
    """スレッド 1 本を、サイトへ送る 1 件分にする。"""
    embed = message.embeds[0]
    fields = [(field.name, field.value) for field in getattr(embed, "fields", [])]

    author = getattr(embed, "author", None)
    author_name = getattr(author, "name", "") or getattr(
        message.author, "display_name", ""
    )
    author_icon = getattr(author, "icon_url", "") or ""

    return {
        "id": str(thread.id),
        "channelId": str(thread.parent_id or ""),
        "messageId": str(message.id),
        "mode": find_field(fields, KEY_MODE).strip(),
        "platform": find_field(fields, KEY_PLATFORM).strip(),
        "vc": vc_name(guild, find_field(fields, KEY_VC).strip()),
        "message": find_field(fields, KEY_MESSAGE).strip(),
        "authorId": str(getattr(message.author, "id", "") or ""),
        "authorName": str(author_name),
        "authorIcon": str(author_icon),
        "startedAt": to_iso(created_at),
        # 最後の書き込み。スレッドは、ここから auto_archive_duration 分で流れる。
        # まだ書き込みが無ければ、立てた時刻と同じ。
        "lastActivityAt": snowflake_time(getattr(thread, "last_message_id", None))
        or to_iso(created_at),
        # 書き込みの数（参加の目安。参加人数そのものではない）
        "messageCount": to_int(getattr(thread, "message_count", 0)),
        # Discord が「最後の書き込みから何分で流す」設定にしているか（普通は 1440 分）
        "archiveMinutes": to_int(getattr(thread, "auto_archive_duration", 0)),
    }


async def fetch_starter_message(thread, guild=None):
    """スレッドの元になった募集メッセージを取る。

    募集のスレッドは「チャンネルへ送ったメッセージ」から作られているので、元メッセージは
    スレッドの中ではなく親チャンネルにある。スレッドから取ろうとすると 404（Unknown
    Message）になるため、親チャンネルから取る。
    """
    parent = getattr(thread, "parent", None)
    if parent is None and guild is not None:
        parent = guild.get_channel(thread.parent_id)
    if parent is None:
        logger.warning("募集の親チャンネルが分かりませんでした: %s", thread.id)
        return None

    try:
        return await parent.fetch_message(thread.id)
    except Exception as error:  # 1 本読めなくても、他の募集は送る
        logger.warning("募集メッセージを読めませんでした: %s (%s)", thread.id, error)
        return None


async def collect_recruitments(guild, channel_ids) -> list[dict]:
    """いま立っている募集を集める（新しい順）。

    `guild.active_threads()` は discord.py では await が要る（取りに行く）ので、
    ここも await する。取りこぼさないよう、キャッシュではなく Discord に聞く。
    """
    threads = [
        thread for thread in await guild.active_threads() if thread.parent_id in channel_ids
    ]
    threads.sort(key=lambda thread: thread.created_at, reverse=True)

    items = []
    for thread in threads:
        message = await fetch_starter_message(thread, guild)
        if message is None or not message.embeds:
            continue
        items.append(
            build_item(
                thread=thread,
                message=message,
                guild=guild,
                created_at=thread.created_at,
            )
        )
    return items


async def run_each(jobs, on_error) -> None:
    """複数の同期を、片方が失敗しても続けて行う。

    募集を読めない状態が続いたときに、イベントまでずっと古いままになる、という
    ことを無くすため。失敗は on_error（見出し, 例外）に渡して、次へ進む。
    """
    for label, job in jobs:
        try:
            await job()
        except Exception as error:  # 片方の失敗で、もう片方を落とさない
            on_error(label, error)


def build_payload(guild_id, items: list[dict]) -> dict:
    """募集を、サイトへ送る形にする。"""
    return {"guildId": str(guild_id), "recruitments": items}


def event_location(event) -> str:
    """場所を決める。外部のイベントは場所の名前、ボイスやステージはチャンネル名。"""
    location = getattr(event, "location", None)
    if location:
        return str(location)

    channel = getattr(event, "channel", None)
    if channel is not None:
        return str(getattr(channel, "name", "") or "")

    return ""


def is_upcoming_event(event) -> bool:
    """これから、または開催中のイベントか。終わったもの・取り消されたものは送らない。"""
    return getattr(event.status, "name", "") not in {"completed", "canceled"}


def build_event(event) -> dict:
    """Discord のイベント 1 件を、サイトへ送る 1 件分にする。"""
    end_time = getattr(event, "end_time", None)
    cover = getattr(event, "cover_image", None)

    return {
        "id": str(event.id),
        "name": str(event.name or ""),
        "description": str(event.description or ""),
        "location": event_location(event),
        "coverUrl": str(getattr(cover, "url", "") or ""),
        "url": str(getattr(event, "url", "") or ""),
        "userCount": int(getattr(event, "user_count", 0) or 0),
        "startAt": to_iso(event.start_time),
        "endAt": to_iso(end_time) if end_time else "",
    }


async def collect_events(guild) -> list[dict]:
    """これからのイベントを集める（始まる順）。"""
    events = await guild.fetch_scheduled_events(with_counts=False)

    items = [build_event(event) for event in events if is_upcoming_event(event)]
    items.sort(key=lambda item: item["startAt"])
    return items


def build_events_payload(guild_id, items: list[dict]) -> dict:
    """イベントを、サイトへ送る形にする。"""
    return {"guildId": str(guild_id), "events": items}


def achievement_master(role_ids, role_names: dict) -> list[dict]:
    """実績の一覧を、サイトへ送る形にする。

    名前は Discord のロール名をそのまま使う（説明やカテゴリは Discord 側にも無い）。
    名前が引けないロール（消されたもの）は送らない。順番は role_ids のとおり。
    """
    return [
        {"id": str(role_id), "name": str(role_names[role_id])}
        for role_id in role_ids
        if role_id in role_names
    ]


# 表示名の長さ（サイト側の MEMBER_NAME_MAX と同じ）
MEMBER_NAME_MAX = 80


def achievement_member(user_id, unlocked, title=None, name="") -> dict:
    """1 人分を、サイトへ送る形にする。

    unlocked は解放済みのロール ID の集まり。並べ方は決めておく（送る内容が毎回同じに
    なるので、変わっていないかを目で見て分かる）。

    name は Discord の表示名。サイトの管理画面で相手を選ぶときに使う（ID だけでは
    誰のことか分からない）。
    """
    return {
        "userId": str(user_id),
        "name": str(name or "")[:MEMBER_NAME_MAX],
        "unlocked": sorted(str(role_id) for role_id in unlocked),
        "title": str(title) if title else "",
    }


def achievement_members(
    unlocked_by_user: dict, title_by_user: dict, name_by_user: dict | None = None
) -> list[dict]:
    """人ごとの実績を、サイトへ送る形にする（ユーザー ID の順）。

    unlocked_by_user は ユーザー ID → 解放済みのロール ID。title_by_user は
    ユーザー ID → いまつけている称号（つけていなければ、入っていない）。
    name_by_user は ユーザー ID → 表示名（分からなければ、名前は空になる）。
    """
    names = name_by_user or {}

    return [
        achievement_member(
            user_id,
            unlocked_by_user[user_id],
            title_by_user.get(user_id),
            names.get(user_id, ""),
        )
        for user_id in sorted(unlocked_by_user)
    ]


def build_achievements_payload(guild_id, achievements: list[dict], members: list[dict]) -> dict:
    """実績を、サイトへ送る形にする。"""
    return {
        "guildId": str(guild_id),
        "achievements": achievements,
        "members": members,
    }


def is_achievements_due(last_sent_at, now, minutes: int) -> bool:
    """実績を送る番か。一度も送っていなければ、すぐ送る。

    実績の付与は手動でたまにしか起きない。5 分ごとに送ると、D1 の無料枠（1 日の書き込み
    行数）を無駄に使うので、間隔をあけて送る。
    """
    if last_sent_at is None:
        return True

    return (now - last_sent_at).total_seconds() >= minutes * 60
