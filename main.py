import os

from threading import Thread
import discord
from discord.ext import commands
from flask import Flask

from cogs.mypage import MyPageView
from cogs.recruitment import RecruitmentPanelView
from config import DISCORD_TOKEN

# Renderのポート検知を通す軽量Flaskアプリ
app = Flask("")


@app.route("/")
def home():
    return "Bot is alive!"


def run_http():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


Thread(target=run_http).start()

# Bot設定
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def setup_hook():
    """Bot起動準備時にCogを一括ロード"""
    # MyPageViewのボタン永続化
    bot.add_view(MyPageView())
    bot.add_view(RecruitmentPanelView())

    # cogsフォルダ内の全pyファイルを自動ロード
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py"):
            await bot.load_extension(f"cogs.{filename[:-3]}")


@bot.event
async def on_ready():
    # サーバー指定でコマンドを即時登録・同期
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    synced = await bot.tree.sync(guild=guild)

    print(f"[{len(synced)}] 件のスラッシュコマンドを即時同期しました！")
    print(f"Logged in as {bot.user}")

    # スラッシュコマンドを同期
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    print(f"Logged in as {bot.user}")


bot.run(DISCORD_TOKEN)