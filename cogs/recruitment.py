from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from config import (
    CHANNEL_CONSOLE,
    CHANNEL_TMOD,
    CHANNEL_VANILLA,
    RECRUIT_CHANNELS,
    ROLE_IDS,
    VC_CHANNEL_IDS,
    WEB_SYNC_BASE,
)
from web_sync import collect_recruitments, deadline_of, remaining_text, to_unix

# 一覧に出す募集の上限（Embed の項目は 25 まで。それ以上は先頭だけ出す）
LIST_MAX = 20


def author_line(item: dict) -> str:
    """「募集主: **名前**（3 時間前）」。時刻が読めないときは名前だけ。"""
    name = item.get("authorName") or "（名前不明）"
    seconds = to_unix(item.get("startedAt"))

    return f"募集主: **{name}**" + (f"（<t:{seconds}:R>）" if seconds else "")


def activity_line(item: dict) -> str:
    """「書き込み 6 件 ・ 最後の書き込み 3 分前」"""
    parts = [f"書き込み {int(item.get('messageCount') or 0)} 件"]

    seconds = to_unix(item.get("lastActivityAt"))
    if seconds:
        parts.append(f"最後の書き込み <t:{seconds}:R>")

    return " ・ ".join(parts)


# ステップ2：テキスト入力フォーム（モーダル）
class RecruitModal(discord.ui.Modal, title="募集詳細の入力"):

    def __init__(self, mode: str, platform: str, vc: str):
        super().__init__()
        self.mode = mode
        self.platform = platform
        self.vc = vc

    message = discord.ui.TextInput(
        label="メッセージ（時間・自由記入など）",
        style=discord.TextStyle.paragraph,
        placeholder="例: 本日21:00〜 初心者歓迎！難易度エキスパートで遊びましょう",
        required=True,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # 1. 投稿先チャンネルの分岐判定
        if self.mode == "モッドマルチ":
            target_channel_id = CHANNEL_TMOD
            vc_prefix = "tmodVC"
        elif self.platform in ["Switch", "PS4", "XBOX", "モバイル"]:
            target_channel_id = CHANNEL_CONSOLE
            vc_prefix = "CSVC"
        else:  # PC かつ バニラマルチ
            target_channel_id = CHANNEL_VANILLA
            vc_prefix = "バニラVC"

        target_channel = interaction.guild.get_channel(target_channel_id)

        if not target_channel:
            await interaction.followup.send(
                "⚠️ 投稿先チャンネルが見つかりませんでした。", ephemeral=True
            )
            return

        # 2. メンション対象ロールの取得
        mode_role_id = ROLE_IDS.get(self.mode)
        pf_role_id = ROLE_IDS.get(self.platform)

        mentions = []
        if mode_role_id:
            mentions.append(f"<@&{mode_role_id}>")
        if pf_role_id:
            mentions.append(f"<@&{pf_role_id}>")

        mention_str = " ".join(mentions)

        # 3. VC名の決定とメンション化の処理
        vc_key = f"{vc_prefix}{self.vc}"
        vc_channel_id = VC_CHANNEL_IDS.get(vc_key)
        
        if vc_channel_id:
            vc_display = f"<#{vc_channel_id}>"
        else:
            vc_display = vc_key

        # 4. Embed（埋め込みメッセージ）の生成
        embed = discord.Embed(
            title="⚔️ Terraria マルチ募集",
            color=discord.Color.green(),
        )
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )
        embed.add_field(name="🎮 種類", value=self.mode, inline=True)
        embed.add_field(name="💻 機種", value=self.platform, inline=True)
        embed.add_field(name="🔊 VC", value=vc_display, inline=True)
        embed.add_field(
            name="📝 メッセージ", value=self.message.value, inline=False
        )

        # 5. チャンネルへメッセージを送信
        sent_message = await target_channel.send(content=mention_str, embed=embed)

        # 6. 送信メッセージに対してスレッドを作成
        thread_name = f"【{self.mode}/{self.platform}】{interaction.user.display_name}の募集"
        thread = await sent_message.create_thread(
            name=thread_name,
            auto_archive_duration=1440,
        )

        # 7. 募集主をスレッドに参加させ、スレッド内でメンションを飛ばす
        await thread.add_user(interaction.user)
        await thread.send(
            f"募集スレッドが作成されました！参加希望や質問はこちらのチャットで行ってください。"
        )

        # 8. 完了通知の送信と選択フォーム（ドロップダウン画面）の削除 ★追加部分
        await interaction.followup.send(
            f"✅ {target_channel.mention} に募集を投稿し、スレッドを作成しました！",
            ephemeral=True,
        )
        
        # ドロップダウンがあった元のメッセージ（フォーム画面）を削除
        try:
            await interaction.delete_original_response()
        except Exception:
            pass


# ステップ1：ドロップダウン選択メニュー
class RecruitSelectView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.select(
        placeholder="1. 種類を選択してください",
        options=[
            discord.SelectOption(
                label="バニラマルチ",
                value="バニラマルチ",
                emoji="✋",
            ),
            discord.SelectOption(
                label="モッドマルチ", value="モッドマルチ", emoji="✋"
            ),
        ],
        row=0,
    )
    async def select_mode(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ):
        await interaction.response.defer()

    @discord.ui.select(
        placeholder="2. 機種を選択してください",
        options=[
            discord.SelectOption(label="PC", value="PC", emoji="⌨️"),
            discord.SelectOption(
                label="Switch", value="Switch", emoji="🍇"
            ),
            discord.SelectOption(label="PS4", value="PS4", emoji="🕹️"),
            discord.SelectOption(
                label="XBOX", value="XBOX", emoji="✖️"
            ),
            discord.SelectOption(
                label="モバイル", value="モバイル", emoji="📱"
            ),
        ],
        row=1,
    )
    async def select_platform(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ):
        await interaction.response.defer()

    @discord.ui.select(
        placeholder="3. 使用VCを選択してください",
        options=[
            discord.SelectOption(
                label=f"VC {i}", value=str(i), emoji="🔊"
            )
            for i in range(1, 6)
        ],
        row=2,
    )
    async def select_vc(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ):
        await interaction.response.defer()

    @discord.ui.button(
        label="次へ（詳細入力）",
        style=discord.ButtonStyle.success,
        row=3,
    )
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # 選択の未入力チェック
        mode_select = self.children[0]
        platform_select = self.children[1]
        vc_select = self.children[2]

        if (
            not mode_select.values
            or not platform_select.values
            or not vc_select.values
        ):
            await interaction.response.send_message(
                "⚠️ 種類・機種・VCのすべての項目を選択してください。",
                ephemeral=True,
            )
            return

        selected_mode = mode_select.values[0]
        selected_platform = platform_select.values[0]
        selected_vc = vc_select.values[0]

        # モーダルを開く
        modal = RecruitModal(selected_mode, selected_platform, selected_vc)
        await interaction.response.send_modal(modal)


# 永続パネル用ビュー（「募集を開始」ボタン）
class RecruitmentPanelView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="募集を開始する",
        style=discord.ButtonStyle.primary,
        custom_id="start_recruitment_button",
        emoji="⚔️",
    )
    async def start_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        view = RecruitSelectView()
        await interaction.response.send_message(
            "募集内容を選択して「次へ」を押してください：",
            view=view,
            ephemeral=True,
        )


# Cog定義
class RecruitmentCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="setup_recruitment",
        description="【管理者専用】Terrariaマルチ募集パネルを設置",
    )
    @app_commands.default_permissions(administrator=True)
    async def setup_recruitment(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🌲 Terraria マルチ募集パネル",
            description="下の「募集を開始する」ボタンを押すと、マルチ募集を作成できます。",
            color=discord.Color.green(),
        )
        await interaction.channel.send(embed=embed, view=RecruitmentPanelView())
        await interaction.response.send_message(
            "募集パネルを設置しました！", ephemeral=True
        )

    # 募集の管理（一覧と締め切り） ------------------------------------------------
    #
    # Discord のスレッドは、最後の書き込みから一定時間（普通は 24 時間）で自動的に流れる。
    # それより早く片付けたいときや、いま立っているものを一度に見たいときのためのコマンド。

    async def recruit_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """いま立っている募集から選べるようにする。

        オートコンプリートは数秒で返す必要があるので、スレッドの一覧だけを取る
        （メッセージの中身は読まない。スレッドの名前で選ぶ）。
        """
        guild = interaction.guild
        if guild is None:
            return []

        threads = [
            thread
            for thread in await guild.active_threads()
            if thread.parent_id in RECRUIT_CHANNELS
        ]
        threads.sort(key=lambda thread: thread.created_at, reverse=True)

        current = current.strip().lower()
        choices = []
        for thread in threads:
            if current and current not in thread.name.lower():
                continue
            choices.append(app_commands.Choice(name=thread.name[:100], value=str(thread.id)))

        return choices[:25]

    @app_commands.command(
        name="recruit_list",
        description="【管理者専用】いま立っているマルチ募集を確認",
    )
    @app_commands.default_permissions(administrator=True)
    async def recruit_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("サーバーの中で使ってください。")
            return

        items = await collect_recruitments(guild, RECRUIT_CHANNELS)
        now = datetime.now(timezone.utc)

        embed = discord.Embed(
            title=f"⚔️ いま立っている募集（{len(items)} 件）",
            color=discord.Color.green(),
        )

        if not items:
            embed.description = "いま立っている募集はありません。"
        else:
            for item in items[:LIST_MAX]:
                lines = [
                    author_line(item),
                    activity_line(item),
                    remaining_text(deadline_of(item), now),
                    f"[スレッドを開く](https://discord.com/channels/{guild.id}/{item['id']})",
                ]
                embed.add_field(
                    name=f"{item.get('mode') or '？'} / {item.get('platform') or '？'}",
                    value="\n".join(line for line in lines if line),
                    inline=False,
                )

            if len(items) > LIST_MAX:
                embed.description = (
                    f"多いので、新しい {LIST_MAX} 件だけ出しています"
                    f"（ほか {len(items) - LIST_MAX} 件）。"
                )

        embed.set_footer(
            text=f"締め切るときは /recruit_close ／ サイト: {WEB_SYNC_BASE}/recruit"
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="recruit_close",
        description="【管理者専用】募集を締め切る（スレッドを閉じて、書き込めなくする）",
    )
    @app_commands.describe(recruitment="締め切る募集（入力を始めると候補が出ます）")
    @app_commands.autocomplete(recruitment=recruit_autocomplete)
    @app_commands.default_permissions(administrator=True)
    async def recruit_close(self, interaction: discord.Interaction, recruitment: str):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("サーバーの中で使ってください。")
            return

        try:
            thread_id = int(recruitment)
        except (TypeError, ValueError):
            await interaction.followup.send("候補から募集を選んでください。")
            return

        try:
            target = await guild.fetch_channel(thread_id)
        except discord.NotFound:
            await interaction.followup.send(
                "その募集は見つかりませんでした（すでに流れているかもしれません）。"
            )
            return
        except discord.HTTPException as error:
            await interaction.followup.send(f"募集を開けませんでした: {error}")
            return

        if (
            not isinstance(target, discord.Thread)
            or target.parent_id not in RECRUIT_CHANNELS
        ):
            await interaction.followup.send("それは募集のスレッドではありません。")
            return

        if target.archived:
            await interaction.followup.send("その募集はすでに締め切られています。")
            return

        try:
            # 参加していた人にも分かるように、締め切ったことを書いてから閉じる。
            # locked にすると、書き込みでスレッドが生き返らない（締め切りの意味が無くなる）。
            await target.send(
                f"🔒 この募集は {interaction.user.mention} の操作で締め切られました。"
                "\nまた遊ぶときは、「募集を開始する」から新しい募集を立ててください。"
            )
            await target.edit(archived=True, locked=True)
        except discord.HTTPException as error:
            await interaction.followup.send(f"締め切れませんでした: {error}")
            return

        await interaction.followup.send(
            f"✅ 募集を締め切りました。サイトの一覧からも、まもなく消えます。\n{target.mention}"
        )

        # サイトからも早く消えるように、次の定期同期を待たずに知らせる。
        # 送れなくても、次の定期同期が拾うので、ここで失敗しても構わない。
        sync = self.bot.get_cog("SiteSyncCog")
        if sync is None:
            return
        try:
            await sync.sync_once()
        except Exception as error:
            print(f"締め切ったあとの同期でエラーが起きました: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RecruitmentCog(bot))