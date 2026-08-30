import os
import redis
import discord
from discord.ext import commands
from threading import Thread
from flask import Flask

# Renderのポート検知を通すための軽量Flaskアプリ
app = Flask("")

@app.route("/")
def home():
    return "Bot is alive!"

def run_http():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# スレッドでHTTPサーバーを並行起動
Thread(target=run_http).start()

# --------------------------------------------------
# Redis & Bot 設定
# --------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
r = redis.from_url(REDIS_URL, decode_responses=True)

ACHIEVEMENT_ROLE_IDS = [
    1543500048245981275,  # 実績AロールID
    1543500112456585267,  # 実績BロールID
    1543500149798473828,  # 実績CロールID
]

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# --------------------------------------------------
# DB操作用関数
# --------------------------------------------------
def get_user_achievements(user_id: int) -> list[int]:
    """ユーザーが解放済みの実績ロールIDを取得"""
    role_ids = r.smembers(f"user:{user_id}:achievements")
    return [int(rid) for rid in role_ids]

def grant_achievement(user_id: int, role_id: int):
    """ユーザーに実績を解放"""
    r.sadd(f"user:{user_id}:achievements", role_id)


# --------------------------------------------------
# 1. ユーザー用：称号着脱モーダル
# --------------------------------------------------
class TitleSelectModal(discord.ui.Modal, title="称号の着脱・変更"):
    # モーダル内に配置するドロップダウン（Select）
    title_select = discord.ui.Select(
        placeholder="装着する称号を1つ選んでください",
        min_values=1,
        max_values=1,
    )

    def __init__(self, available_roles: list[discord.Role]):
        super().__init__()
        self.available_roles = available_roles

        # 選択肢の構築
        options = [
            discord.SelectOption(label=role.name, value=str(role.id))
            for role in available_roles
        ]
        options.append(
            discord.SelectOption(
                label="称号をつけない",
                value="none",
                description="すべての実績称号を外します"
            )
        )
        
        self.title_select.options = options
        self.add_item(self.title_select)

    async def on_submit(self, interaction: discord.Interaction):
        """モーダルの送信ボタンが押された時の処理"""
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        guild = interaction.guild
        selected_value = self.title_select.values[0]

        try:
            # 1. 現在セットされている対象の実績系ロールをすべて剥奪
            roles_to_remove = [role for role in user.roles if role.id in ACHIEVEMENT_ROLE_IDS]
            if roles_to_remove:
                await user.remove_roles(*roles_to_remove)

            # 2. 選択されたロールを1つだけ付与
            if selected_value != "none":
                selected_role = guild.get_role(int(selected_value))
                if selected_role:
                    await user.add_roles(selected_role)
                    await interaction.followup.send(f"称号を **{selected_role.name}** に変更しました！", ephemeral=True)
                    return

            await interaction.followup.send("称号を外しました。", ephemeral=True)

        except discord.Forbidden:
            await interaction.followup.send("エラー: Botの権限が不足しているか、Botのロールが対象の称号ロールより下に配置されています。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"エラーが発生しました: {e}", ephemeral=True)


class MyPageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="称号着脱", style=discord.ButtonStyle.primary, custom_id="mypage_title_button")
    async def title_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        guild = interaction.guild

        # 1. ユーザーが現在実際に持っている対象ロールをチェックし、DBになければ自動登録する
        for role in user.roles:
            if role.id in ACHIEVEMENT_ROLE_IDS:
                grant_achievement(user.id, role.id)

        # 2. DBからこのユーザーが獲得している実績ロールID一覧を取得
        unlocked_ids = get_user_achievements(user.id)

        # 3. サーバー内に存在する該当ロールオブジェクトを取得
        available_roles = []
        for rid in unlocked_ids:
            role = guild.get_role(rid)
            if role:
                available_roles.append(role)

        if not available_roles:
            await interaction.response.send_message("装着できる実績称号を所持していません。", ephemeral=True)
            return

        # モーダルダイアログを起動
        await interaction.response.send_modal(TitleSelectModal(available_roles))


# --------------------------------------------------
# 2. 管理者用：実績解放登録モーダル
# --------------------------------------------------
class AdminGiveModal(discord.ui.Modal, title="【管理者専用】実績権限の解放"):
    user_id_input = discord.ui.TextInput(
        label="対象ユーザーのID",
        placeholder="例: 123456789012345678",
        min_length=17,
        max_length=20,
        required=True
    )
    role_id_input = discord.ui.TextInput(
        label="解放する実績ロールのID",
        placeholder="例: 1543500048245981275",
        min_length=17,
        max_length=20,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            target_user_id = int(self.user_id_input.value.strip())
            target_role_id = int(self.role_id_input.value.strip())
        except ValueError:
            await interaction.followup.send("IDは数字のみで入力してください。", ephemeral=True)
            return

        if target_role_id not in ACHIEVEMENT_ROLE_IDS:
            await interaction.followup.send("指定されたロールIDは対象の実績ロールリストに含まれていません。", ephemeral=True)
            return

        # DBに保存
        grant_achievement(target_user_id, target_role_id)

        target_member = interaction.guild.get_member(target_user_id)
        target_role = interaction.guild.get_role(target_role_id)

        user_name = target_member.mention if target_member else f"ID: {target_user_id}"
        role_name = f"**{target_role.name}**" if target_role else f"ID: {target_role_id}"

        await interaction.followup.send(f"{user_name} に実績 {role_name} の装着権限を解放（DB登録）しました！", ephemeral=True)


# --------------------------------------------------
# コマンド定義
# --------------------------------------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def give_achievement(ctx):
    """管理者用：モーダルを開いて手動でユーザーに実績権限を与えるコマンド"""
    # プレフィックスコマンド実行時にそのままモーダルを呼び出すことはDiscord仕様上不可能なため、
    # ボタン1つだけのビューを出してモーダルへ遷移させます
    class OpenAdminModalView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)

        @discord.ui.button(label="実績解放フォームを開く", style=discord.ButtonStyle.danger)
        async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_modal(AdminGiveModal())

    await ctx.send("下のボタンを押して実績解放モーダルを開いてください：", view=OpenAdminModalView())


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_mypage(ctx):
    """マイページパネル設置コマンド"""
    embed = discord.Embed(
        title="👤 マイページ / 称号設定",
        description="下のボタンから獲得済みの実績称号（ロール）を着脱できます。\n※つけられる称号は同時に1つまでです。",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed, view=MyPageView())
    await ctx.message.delete()


bot.run(os.getenv("DISCORD_TOKEN"))