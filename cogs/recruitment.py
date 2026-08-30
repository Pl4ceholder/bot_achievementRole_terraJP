import discord
from discord import app_commands
from discord.ext import commands
from config import (
    CHANNEL_CONSOLE,
    CHANNEL_TMOD,
    CHANNEL_VANILLA,
    ROLE_IDS,
    VC_CHANNEL_IDS,
)


# ステップ2：テキスト入力フォーム（モーダル）
class RecruitModal(discord.ui.Modal, title="募集詳細の入力"):

    def __init__(self, mode: str, platform: str, vc: str):
        super().__init__()
        self.mode = mode
        self.platform = platform
        self.vc = vc

    connection_info = discord.ui.TextInput(
        label="SteamID または IP:Port",
        placeholder="例: 123456789 または 192.168.1.1:7777 (未定の場合は「なし」)",
        required=False,
        max_length=100,
    )

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

        conn_val = (
            self.connection_info.value if self.connection_info.value else "なし"
        )
        embed.add_field(
            name="🔗 SteamID / IP:Port", value=conn_val, inline=False
        )
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


async def setup(bot: commands.Bot):
    await bot.add_cog(RecruitmentCog(bot))