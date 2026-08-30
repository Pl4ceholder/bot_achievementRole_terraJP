import os
from threading import Thread

import discord
from discord.ext import commands
from flask import Flask

from cogs.mypage import MyPageView
from cogs.recruitment import RecruitmentPanelView
from config import DISCORD_TOKEN, GUILD_ID  # GUILD_ID をインポート

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
    """Bot起動準備時にCogを一括ロードおよびボタンの永続化"""
    bot.add_view(MyPageView())
    bot.add_view(RecruitmentPanelView())

    for filename in os.listdir("./cogs"):
        if filename.endswith(".py"):
            await bot.load_extension(f"cogs.{filename[:-3]}")


@bot.event
async def on_ready():
    # GUILD_ID が環境変数に設定されている場合は即時同期を行う
    if GUILD_ID:
        try:
            guild_obj = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild_obj)
            synced = await bot.tree.sync(guild=guild_obj)
            print(
                f"[{len(synced)}] 件のスラッシュコマンドを Guild({GUILD_ID}) に即時同期しました！"
            )
        except Exception as e:
            print(f"Failed to sync commands to guild: {e}")
    else:
        # GUILD_ID 未設定時は全体同期（反映に時間がかかる場合があります）
        try:
            synced = await bot.tree.sync()
            print(
                f"[{len(synced)}] 件のスラッシュコマンドをグローバル同期しました！"
            )
        except Exception as e:
            print(f"Failed to sync global commands: {e}")

    print(f"Logged in as {bot.user}")


bot.run(DISCORD_TOKEN)