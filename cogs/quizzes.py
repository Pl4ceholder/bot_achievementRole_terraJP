import random
import unicodedata

import discord
from discord import app_commands
from discord.ext import commands

from database import (
    create_quiz,
    delete_quiz,
    get_quiz_categories,
    get_quizzes,
)


def _split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _normalize_answer(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


class QuizCreateModal(discord.ui.Modal, title="クイズ登録"):
    question = discord.ui.TextInput(label="問題文", max_length=1000)
    category = discord.ui.TextInput(
        label="カテゴリ（任意）", required=False, max_length=100
    )
    entries = discord.ui.TextInput(
        label="選択肢 または 正解（1行に1つ）",
        style=discord.TextStyle.paragraph,
        placeholder="選択式: 選択肢を改行で入力\n記述式: 正解を改行で入力",
        max_length=4000,
    )
    correct = discord.ui.TextInput(
        label="選択式の正解（選択肢と完全一致）",
        required=False,
        max_length=100,
    )

    def __init__(self, kind: str):
        super().__init__(title="選択式クイズ登録" if kind == "choice" else "記述式クイズ登録")
        self.kind = kind
        if kind == "text":
            self.remove_item(self.correct)

    async def on_submit(self, interaction: discord.Interaction):
        entries = _split_lines(self.entries.value)
        if self.kind == "choice":
            choices = entries
            answer = self.correct.value.strip()
            if len(choices) < 2 or len(choices) > 25:
                await interaction.response.send_message(
                    "選択式は選択肢を2〜25個、1行に1つ入力してください。", ephemeral=True
                )
                return
            if not answer or answer not in choices:
                await interaction.response.send_message(
                    "正解は選択肢の中から、同じ表記で入力してください。", ephemeral=True
                )
                return
            answers = [answer]
        else:
            choices = []
            answers = entries
            if not answers:
                await interaction.response.send_message(
                    "記述式の正解を1行以上入力してください。", ephemeral=True
                )
                return

        quiz_id = create_quiz(
            self.question.value.strip(),
            self.kind,
            self.category.value.strip() or None,
            choices,
            answers,
        )
        await interaction.response.send_message(
            f"クイズを登録しました。ID: `{quiz_id}`", ephemeral=True
        )


class TextAnswerModal(discord.ui.Modal, title="クイズに回答"):
    answer = discord.ui.TextInput(label="回答", max_length=500)

    def __init__(self, quiz: dict):
        super().__init__()
        self.quiz = quiz

    async def on_submit(self, interaction: discord.Interaction):
        correct = any(
            _normalize_answer(self.answer.value) == _normalize_answer(answer)
            for answer in self.quiz["answers"]
        )
        result = "✅ 正解です！" if correct else "❌ 不正解です。"
        await interaction.response.send_message(
            f"{result}\n正解: {' / '.join(self.quiz['answers'])}", ephemeral=True
        )


class QuizSessionView(discord.ui.View):
    def __init__(self, quiz: dict):
        super().__init__(timeout=300)
        self.quiz = quiz
        if quiz["kind"] == "choice":
            self.add_item(QuizAnswerSelect(quiz))
        else:
            self.add_item(TextAnswerButton(quiz))


class QuizAnswerSelect(discord.ui.Select):
    def __init__(self, quiz: dict):
        self.quiz = quiz
        options = [discord.SelectOption(label=choice[:100], value=str(index)) for index, choice in enumerate(quiz["choices"])]
        super().__init__(placeholder="回答を選んでください", options=options)

    async def callback(self, interaction: discord.Interaction):
        answer = self.quiz["choices"][int(self.values[0])]
        correct = _normalize_answer(answer) == _normalize_answer(self.quiz["answers"][0])
        result = "✅ 正解です！" if correct else "❌ 不正解です。"
        await interaction.response.send_message(
            f"{result}\n正解: {self.quiz['answers'][0]}", ephemeral=True
        )


class TextAnswerButton(discord.ui.Button):
    def __init__(self, quiz: dict):
        super().__init__(label="回答する", style=discord.ButtonStyle.primary)
        self.quiz = quiz

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TextAnswerModal(self.quiz))


class QuizCategorySelect(discord.ui.Select):
    def __init__(self, categories: list[str]):
        options = [discord.SelectOption(label=category[:100], value=category) for category in categories[:25]]
        super().__init__(placeholder="出題するカテゴリを選んでください", options=options)

    async def callback(self, interaction: discord.Interaction):
        await send_random_quiz(interaction, self.values[0])


class QuizCategoryView(discord.ui.View):
    def __init__(self, categories: list[str]):
        super().__init__(timeout=60)
        self.add_item(QuizCategorySelect(categories))


async def send_random_quiz(interaction: discord.Interaction, category: str | None = None):
    quizzes = get_quizzes(category)
    if not quizzes:
        message = "このカテゴリにはクイズがありません。" if category else "登録済みのクイズがありません。"
        await interaction.response.send_message(message, ephemeral=True)
        return

    quiz = random.choice(quizzes)
    embed = discord.Embed(
        title="🧠 クイズ",
        description=quiz["question"],
        color=discord.Color.blurple(),
    )
    if quiz["category"]:
        embed.add_field(name="カテゴリ", value=quiz["category"], inline=True)
    if quiz["kind"] == "choice":
        embed.add_field(name="形式", value="選択式", inline=True)
    else:
        embed.add_field(name="形式", value="記述式", inline=True)
    await interaction.response.send_message(embed=embed, view=QuizSessionView(quiz), ephemeral=True)


class QuizPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="ランダム出題", style=discord.ButtonStyle.primary,
        custom_id="quiz_random_all", emoji="🎲"
    )
    async def random_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_random_quiz(interaction)

    @discord.ui.button(
        label="カテゴリから出題", style=discord.ButtonStyle.secondary,
        custom_id="quiz_random_category", emoji="📚"
    )
    async def random_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        categories = get_quiz_categories()
        if not categories:
            await interaction.response.send_message("カテゴリ付きのクイズがありません。", ephemeral=True)
            return
        await interaction.response.send_message(
            "出題するカテゴリを選んでください：",
            view=QuizCategoryView(categories), ephemeral=True
        )


class QuizCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="quiz_add", description="クイズを登録します")
    @app_commands.describe(kind="クイズの回答形式")
    @app_commands.choices(kind=[
        app_commands.Choice(name="選択式", value="choice"),
        app_commands.Choice(name="記述式", value="text"),
    ])
    @app_commands.default_permissions(administrator=True)
    async def quiz_add(self, interaction: discord.Interaction, kind: str):
        await interaction.response.send_modal(QuizCreateModal(kind))

    @app_commands.command(name="quiz_list", description="登録済みクイズを一覧表示します")
    @app_commands.default_permissions(administrator=True)
    async def quiz_list(self, interaction: discord.Interaction):
        quizzes = get_quizzes()
        if not quizzes:
            await interaction.response.send_message("登録済みのクイズはありません。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        # DiscordのEmbed文字数上限に収まるよう、1メッセージ5問ずつ表示。
        for offset in range(0, len(quizzes), 5):
            embed = discord.Embed(title="登録済みクイズ", color=discord.Color.blurple())
            for quiz in quizzes[offset:offset + 5]:
                kind = "選択式" if quiz["kind"] == "choice" else "記述式"
                details = [f"形式: {kind}", f"カテゴリ: {quiz['category'] or 'なし'}"]
                if quiz["choices"]:
                    details.append("選択肢: " + " / ".join(quiz["choices"]))
                details.append("回答: " + " / ".join(quiz["answers"]))
                embed.add_field(
                    name=f"ID {quiz['id']} · {quiz['question'][:200]}",
                    value="\n".join(details)[:1024], inline=False
                )
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def quiz_id_autocomplete(self, interaction: discord.Interaction, current: str):
        quizzes = get_quizzes()
        return [
            app_commands.Choice(
                name=f"{quiz['id']}: {quiz['question'][:80]}", value=quiz["id"]
            )
            for quiz in quizzes
            if current in quiz["id"]
        ][:25]

    @app_commands.command(name="quiz_delete", description="クイズを削除します")
    @app_commands.autocomplete(quiz_id=quiz_id_autocomplete)
    @app_commands.default_permissions(administrator=True)
    async def quiz_delete(self, interaction: discord.Interaction, quiz_id: str):
        if not delete_quiz(quiz_id):
            await interaction.response.send_message("そのIDのクイズは見つかりません。", ephemeral=True)
            return
        await interaction.response.send_message(f"クイズ ID `{quiz_id}` を削除しました。", ephemeral=True)

    @app_commands.command(name="quiz_panel", description="クイズ出題パネルを設置します")
    @app_commands.default_permissions(administrator=True)
    async def quiz_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🧠 操作クイズ",
            description="ボタンを押すと、登録済みクイズからランダムに出題します。",
            color=discord.Color.blurple(),
        )
        await interaction.channel.send(embed=embed, view=QuizPanelView())
        await interaction.response.send_message("クイズパネルを設置しました。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(QuizCog(bot))
