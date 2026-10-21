"""スターボード（cogs/starboard.py）を、Discord にも Redis にも繋がずに確かめる。

    pip install -r requirements.txt
    python tools/check_starboard.py

確かめること:

- 設定した絵文字だけを数えるか（カスタム絵文字も含む）
- 投稿者本人と Bot を数えないか
- しきい値に届いたときだけ 1 回載せ、届かなければ載せないか
- リアクションが増えたら表示を更新し、減ってしきい値を下回ったら片付けるか
- 元の投稿が消えたとき・スターボードの投稿が消えたときに、記録と投稿を片付けるか
- スターボードのチャンネル自身や、除外したチャンネルを無視するか

Discord には繋がない（ダミーを渡す）。合っていなければ終了コード 1 で終わる。
"""

import asyncio
import os
import sys
import types
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

import cogs.starboard as starboard  # noqa: E402
import database  # noqa: E402

GUILD_ID = 999000111222333444
SOURCE_CHANNEL_ID = 111122223333444455
STARBOARD_CHANNEL_ID = 555566667777888899


def http_error(cls, status=404, reason="Not Found", message="Unknown Message"):
    """本物と同じ形の Discord のエラーを作る。"""
    response = types.SimpleNamespace(status=status, reason=reason)
    return cls(response, {"message": message, "code": 10008})


class FakeUser:
    def __init__(self, user_id, name, bot=False):
        self.id = user_id
        self.name = name
        self.display_name = name
        self.bot = bot
        self.display_avatar = types.SimpleNamespace(
            url=f"https://example.com/{user_id}.png"
        )


class FakeAttachment:
    def __init__(self, filename, url, content_type):
        self.filename = filename
        self.url = url
        self.content_type = content_type


class FakeReaction:
    def __init__(self, emoji, users):
        self.emoji = emoji
        self.users_list = users

    @property
    def count(self):
        return len(self.users_list)

    async def users(self):
        for user in self.users_list:
            yield user


class FakeMessage:
    next_id = 2001

    def __init__(self, message_id, author, content, channel, reactions=None):
        self.id = message_id
        self.author = author
        self.content = content
        self.channel = channel
        self.reactions = reactions or []
        self.attachments = []
        self.embeds = []
        self.created_at = datetime.now(timezone.utc)


class FakePostedMessage:
    def __init__(self, message_id, embed, channel):
        self.id = message_id
        self.embed = embed
        self.channel = channel
        self.edits = []

    async def edit(self, embed=None, allowed_mentions=None):
        self.edits.append(embed)
        self.embed = embed

    async def delete(self):
        self.channel.messages.pop(self.id, None)
        self.channel.deleted.append(self.id)


class FakeChannel:
    next_id = 900000000000000001

    def __init__(self, channel_id):
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.messages = {}
        self.sent = []
        self.deleted = []

    async def fetch_message(self, message_id):
        message = self.messages.get(message_id)
        if message is None:
            raise http_error(discord.NotFound)
        return message

    async def send(self, embed=None, allowed_mentions=None):
        posted = FakePostedMessage(FakeChannel.next_id, embed, self)
        FakeChannel.next_id += 1
        self.messages[posted.id] = posted
        self.sent.append(posted)
        return posted


class FakeGuild:
    def __init__(self, channels):
        self.id = GUILD_ID
        self.channels = {channel.id: channel for channel in channels}

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id):
        channel = self.channels.get(channel_id)
        if channel is None:
            raise http_error(discord.NotFound)
        return channel


class FakeBot:
    def __init__(self, guild):
        self.guild = guild
        self.user = FakeUser(999000000000000001, "Bot", bot=True)

    def get_guild(self, guild_id):
        return self.guild if guild_id == GUILD_ID else None

    def get_channel(self, channel_id):
        return self.guild.get_channel(channel_id)

    async def fetch_channel(self, channel_id):
        return await self.guild.fetch_channel(channel_id)


class FakeStore:
    """database.py のスターボード用の関数を、メモリ上で置き換える。"""

    def __init__(self):
        self.entries = {}
        self.reverse = {}

    def get_entry(self, message_id):
        return self.entries.get(message_id)

    def get_source(self, starboard_message_id):
        return self.reverse.get(starboard_message_id)

    def save(self, message_id, starboard_message_id, starboard_channel_id, count):
        old = self.entries.get(message_id)
        if old and old.get("starboard_message_id") != str(starboard_message_id):
            self.reverse.pop(int(old["starboard_message_id"]), None)

        self.entries[message_id] = {
            "starboard_message_id": str(starboard_message_id),
            "starboard_channel_id": str(starboard_channel_id),
            "count": count,
        }
        self.reverse[starboard_message_id] = message_id

    def delete(self, message_id):
        entry = self.entries.pop(message_id, None)
        if entry:
            self.reverse.pop(int(entry["starboard_message_id"]), None)

    def install(self):
        original = (
            starboard.get_starboard_entry,
            starboard.get_starboard_source,
            starboard.save_starboard_entry,
            starboard.delete_starboard_entry,
        )
        starboard.get_starboard_entry = self.get_entry
        starboard.get_starboard_source = self.get_source
        starboard.save_starboard_entry = self.save
        starboard.delete_starboard_entry = self.delete
        return original

    @staticmethod
    def restore(original):
        (
            starboard.get_starboard_entry,
            starboard.get_starboard_source,
            starboard.save_starboard_entry,
            starboard.delete_starboard_entry,
        ) = original


class FakeRedis:
    """database.py が使う get / set / delete だけを持つ Redis。"""

    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


def check_database() -> bool:
    """スターボードの記録が、Redis の読み書きで壊れないか。"""
    original = database.r
    database.r = FakeRedis()

    try:
        database.save_starboard_entry(10, 20, 30, 4)
        entry = database.get_starboard_entry(10)
        source = database.get_starboard_source(20)

        # スターボードの投稿が作り直されたら、古い逆引きを消す。
        database.save_starboard_entry(10, 21, 30, 5)
        old_source = database.get_starboard_source(20)
        new_source = database.get_starboard_source(21)

        database.delete_starboard_entry(10)
        removed = database.get_starboard_entry(10)
        removed_source = database.get_starboard_source(21)

        return (
            entry is not None
            and entry["starboard_message_id"] == "20"
            and entry["starboard_channel_id"] == "30"
            and entry["count"] == 4
            and source == 10
            and old_source is None
            and new_source == 10
            and removed is None
            and removed_source is None
        )
    finally:
        database.r = original


def make_world():
    source_channel = FakeChannel(SOURCE_CHANNEL_ID)
    board_channel = FakeChannel(STARBOARD_CHANNEL_ID)
    guild = FakeGuild([source_channel, board_channel])
    bot = FakeBot(guild)
    cog = starboard.StarboardCog(bot)

    author = FakeUser(1001, "投稿者")
    message = FakeMessage(
        FakeMessage.next_id, author, "これは良い投稿です", source_channel
    )
    FakeMessage.next_id += 1
    source_channel.messages[message.id] = message

    return source_channel, board_channel, cog, message, author


def payload_for(message, user_id, emoji="⭐", member=None):
    return types.SimpleNamespace(
        guild_id=GUILD_ID,
        channel_id=message.channel.id,
        message_id=message.id,
        user_id=user_id,
        emoji=discord.PartialEmoji.from_str(emoji),
        member=member,
    )


def set_reaction_users(message, users, emoji="⭐"):
    message.reactions = [FakeReaction(emoji, users)]


def configure(threshold=3, emoji="⭐"):
    starboard.STARBOARD_CHANNEL_ID = STARBOARD_CHANNEL_ID
    starboard.STARBOARD_EMOJI = emoji
    starboard.STARBOARD_THRESHOLD = threshold
    starboard.STARBOARD_IGNORE_CHANNEL_IDS = frozenset()
    starboard.STARBOARD_IGNORE_BOTS = True
    starboard.STARBOARD_ALLOW_SELF_STAR = False


async def check_emoji() -> bool:
    custom = discord.PartialEmoji(name="star", id=123456789012345678)
    return (
        starboard.same_emoji("⭐", "⭐")
        and starboard.same_emoji("⭐️", "⭐")
        and starboard.same_emoji(custom, "<:star:123456789012345678>")
        and not starboard.same_emoji(custom, "<:star:999999999999999999>")
        and not starboard.same_emoji("⭐", "❤️")
    )


async def check_embed() -> bool:
    source_channel, _, _, message, _ = make_world()
    message.attachments = [
        FakeAttachment("shot.png", "https://example.com/shot.png", "image/png"),
        FakeAttachment("log.txt", "https://example.com/log.txt", "text/plain"),
    ]
    message.embeds = [
        types.SimpleNamespace(
            title="引用元", url="https://example.com", description="説明"
        )
    ]

    embed = starboard.build_embed(message, 4, GUILD_ID)
    fields = {field.name: field.value for field in embed.fields}
    footer = embed.footer.text or ""

    return (
        embed.title == "⭐ 秀逸な投稿"
        and embed.description == "これは良い投稿です"
        and embed.image.url == "https://example.com/shot.png"
        and "https://example.com/log.txt" in fields.get("添付ファイル", "")
        and "引用元" in fields.get("引用", "")
        and "https://discord.com/channels/"
        f"{GUILD_ID}/{source_channel.id}/{message.id}" in fields.get("元の投稿", "")
        and "4 件" in footer
    )


async def check_count() -> bool:
    _, _, _, message, author = make_world()
    humans = [FakeUser(1002, "A"), FakeUser(1003, "B")]
    set_reaction_users(message, [author, *humans, FakeUser(1004, "Bot", bot=True)])

    default = await starboard.count_stars(message, "⭐")

    starboard.STARBOARD_ALLOW_SELF_STAR = True
    with_self = await starboard.count_stars(message, "⭐")
    starboard.STARBOARD_ALLOW_SELF_STAR = False

    return default == 2 and with_self == 3


async def check_threshold_flow() -> bool:
    _, board, cog, message, author = make_world()
    alice = FakeUser(1002, "A")
    bob = FakeUser(1003, "B")
    carol = FakeUser(1004, "C")

    # まだ 2 人。載せない。
    set_reaction_users(message, [author, alice, bob])
    await cog._handle_reaction(payload_for(message, alice.id), added=True)
    if board.sent:
        return False

    # 3 人目で載る。
    set_reaction_users(message, [author, alice, bob, carol])
    await cog._handle_reaction(payload_for(message, carol.id), added=True)
    if len(board.sent) != 1:
        return False

    posted = board.sent[0]
    entry = starboard.get_starboard_entry(message.id)
    if entry is None or entry["starboard_message_id"] != str(posted.id):
        return False

    # もう一度同じリアクションが来ても、二重に載せない（表示だけ更新する）。
    await cog._handle_reaction(payload_for(message, carol.id), added=True)
    if len(board.sent) != 1:
        return False

    # 増えたら、同じスターボードの投稿を更新する。
    dave = FakeUser(1005, "D")
    set_reaction_users(message, [author, alice, bob, carol, dave])
    await cog._handle_reaction(payload_for(message, dave.id), added=True)
    if len(board.sent) != 1 or not posted.edits:
        return False

    # 減ってしきい値を下回ったら、スターボードから消す。
    set_reaction_users(message, [author, alice])
    await cog._handle_reaction(payload_for(message, bob.id), added=False)
    if posted.id in board.messages or starboard.get_starboard_entry(message.id):
        return False

    # また届いたら、載せ直せる。
    set_reaction_users(message, [author, alice, bob, carol])
    await cog._handle_reaction(payload_for(message, carol.id), added=True)
    return len(board.sent) == 2


async def check_removed_without_entry() -> bool:
    """まだ載せていない投稿のリアクションが減っても、読みに行かないか。"""
    source, board, cog, message, author = make_world()
    set_reaction_users(message, [author, FakeUser(1002, "A")])
    await cog._handle_reaction(payload_for(message, 1002), added=False)

    return not board.sent and source.messages == {message.id: message}


async def check_ignored() -> bool:
    source, board, cog, message, author = make_world()
    set_reaction_users(
        message,
        [author, FakeUser(1002, "A"), FakeUser(1003, "B"), FakeUser(1004, "C")],
    )

    # スターボードのチャンネル自身は対象外。
    board_message = FakeMessage(
        3001, author, "スターボードの投稿", board, [FakeReaction("⭐", [author])]
    )
    await cog._handle_reaction(
        payload_for(board_message, author.id), added=True
    )

    # 除外したチャンネルも対象外。
    starboard.STARBOARD_IGNORE_CHANNEL_IDS = frozenset({source.id})
    await cog._handle_reaction(payload_for(message, 1002), added=True)
    starboard.STARBOARD_IGNORE_CHANNEL_IDS = frozenset()

    # 対象ではない絵文字は数えない。
    await cog._handle_reaction(payload_for(message, 1002, emoji="❤️"), added=True)

    # 投稿者本人のリアクションだけでは載らない。
    set_reaction_users(message, [author])
    await cog._handle_reaction(payload_for(message, author.id), added=True)

    return not board.sent


async def check_bot_message() -> bool:
    _, board, cog, message, _ = make_world()
    message.author = FakeUser(1001, "Bot投稿", bot=True)
    set_reaction_users(
        message, [FakeUser(1002, "A"), FakeUser(1003, "B"), FakeUser(1004, "C")]
    )

    await cog._handle_reaction(payload_for(message, 1002), added=True)
    return not board.sent


async def check_source_delete() -> bool:
    _, board, cog, message, author = make_world()
    set_reaction_users(
        message,
        [author, FakeUser(1002, "A"), FakeUser(1003, "B"), FakeUser(1004, "C")],
    )
    await cog._handle_reaction(payload_for(message, 1002), added=True)

    posted = board.sent[0]
    await cog._handle_deleted_message(message.id)

    return (
        posted.id not in board.messages
        and starboard.get_starboard_entry(message.id) is None
    )


async def check_board_delete() -> bool:
    _, board, cog, message, author = make_world()
    set_reaction_users(
        message,
        [author, FakeUser(1002, "A"), FakeUser(1003, "B"), FakeUser(1004, "C")],
    )
    await cog._handle_reaction(payload_for(message, 1002), added=True)

    posted = board.sent[0]
    await cog._handle_deleted_message(posted.id)

    # スターボード側を消しただけなので、記録を消す（勝手に載せ直さない）。
    return starboard.get_starboard_entry(message.id) is None


async def check_edit() -> bool:
    _, board, cog, message, author = make_world()
    set_reaction_users(
        message,
        [author, FakeUser(1002, "A"), FakeUser(1003, "B"), FakeUser(1004, "C")],
    )
    await cog._handle_reaction(payload_for(message, 1002), added=True)

    message.content = "編集後の投稿です"
    await cog._handle_edit(payload_for(message, 1002))

    return bool(board.sent) and board.sent[0].embed.description == "編集後の投稿です"


async def check_disabled() -> bool:
    _, board, cog, message, _ = make_world()
    starboard.STARBOARD_CHANNEL_ID = None
    set_reaction_users(
        message, [FakeUser(1002, "A"), FakeUser(1003, "B"), FakeUser(1004, "C")]
    )

    await cog._handle_reaction(payload_for(message, 1002), added=True)
    return not board.sent


async def main() -> None:
    checks = []
    store = FakeStore()

    configure()
    original = store.install()
    try:
        checks.extend(
            [
                ("記録を Redis に出し入れできる", check_database()),
                ("設定した絵文字だけを数える", await check_emoji()),
                ("埋め込みに本文・画像・元の投稿へのリンクを入れる", await check_embed()),
                ("投稿者本人と Bot を数えない", await check_count()),
                ("しきい値で載せ、増減に追従し、下回ったら消す", await check_threshold_flow()),
                ("まだ載せていない投稿のリアクション削除では読みに行かない", await check_removed_without_entry()),
                ("スターボード自身・除外チャンネル・対象外の絵文字を無視する", await check_ignored()),
                ("Bot の投稿は載せない", await check_bot_message()),
                ("元の投稿が消えたらスターボードからも消す", await check_source_delete()),
                ("スターボード側が消されたら記録だけ消す", await check_board_delete()),
                ("元の投稿の編集をスターボードへ反映する", await check_edit()),
                ("設定が無いときは動かない", await check_disabled()),
            ]
        )
    finally:
        FakeStore.restore(original)

    for title, ok in checks:
        print(("OK   " if ok else "ダメ  ") + title)

    if not all(ok for _, ok in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
