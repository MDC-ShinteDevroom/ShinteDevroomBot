import asyncio
import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")
COGS_DIR = Path(__file__).parent / "cogs"

# 管理者権限チェックの対象外にする（誰でも実行可能な）コマンド名
PUBLIC_COMMANDS = {
    "gr3nja",
    "join",
    "play",
    "pause",
    "skip",
    "resume",
    "leave",
    "stop",
    "queue",
    "nowplaying",
}

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")


async def admin_only_check(ctx: commands.Context) -> bool:
    """PUBLIC_COMMANDS 以外の全コマンドを管理者限定にする共通チェック"""
    if ctx.command is not None and ctx.command.qualified_name in PUBLIC_COMMANDS:
        return True
    if ctx.guild is None:
        return False
    if ctx.author.guild_permissions.administrator:
        return True
    raise commands.MissingPermissions(["administrator"])


def get_prefix(bot: commands.Bot, message: discord.Message) -> str:
    """%toban / %tobancancel のときだけ % を使い、それ以外は ! を使う"""
    if message.content.startswith("%toban"):
        return "%"
    return COMMAND_PREFIX


class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # メッセージ内容を使う場合は必須
        intents.members = True  # メンバー情報を使う場合

        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            help_command=commands.DefaultHelpCommand(),
        )

    async def setup_hook(self):
        """起動時に一度だけ呼ばれる。Cog の読み込みなどを行う"""
        await self.load_all_cogs()

        # すべての ! コマンドを管理者権限のみ実行可能にする（PUBLIC_COMMANDS は例外）
        self.add_check(admin_only_check)

        # スラッシュコマンドを同期する場合はここで実行
        # 特定サーバーのみ即時反映したい場合は guild=discord.Object(id=...) を指定
        synced = await self.tree.sync()
        logger.info("スラッシュコマンドを %s 件同期しました", len(synced))

    async def load_all_cogs(self):
        """cogs/ ディレクトリ以下の *.py を自動読み込みする"""
        if not COGS_DIR.is_dir():
            logger.warning("Cogs ディレクトリが見つかりません: %s", COGS_DIR)
            return

        for path in COGS_DIR.rglob("*.py"):
            if path.stem.startswith("_"):
                continue  # _example.py のようなファイルはスキップ

            # cogs/sub/foo.py -> cogs.sub.foo
            rel_path = path.relative_to(COGS_DIR.parent).with_suffix("")
            extension = ".".join(rel_path.parts)

            try:
                await self.load_extension(extension)
                logger.info(f"Cog を読み込みました: {extension}")
            except Exception as e:
                logger.error(f"Cog の読み込みに失敗しました: {extension} -> {e}")

    async def on_ready(self):
        logger.info("ログインしました: %s (ID: %s)", self.user, self.user.id)
        logger.info("接続サーバー数: %s", len(self.guilds))
        await self.change_presence(
            activity=discord.Game(name=f"{COMMAND_PREFIX}help で使い方を確認")
        )

    async def on_command_error(self, ctx, error):
        """テキストコマンドの共通エラーハンドリング"""
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"引数が不足しています: `{error.param.name}`")
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("このコマンドを実行するには管理者権限が必要です。")
            return

        logger.exception("コマンド実行中にエラーが発生しました", exc_info=error)
        await ctx.send("コマンドの実行中にエラーが発生しました。")


bot = MyBot()


@bot.command(name="reload")
@commands.is_owner()
async def reload_cog(ctx, extension: str):
    """指定した Cog をリロードする（例: !reload cogs.ping）"""
    try:
        await bot.reload_extension(extension)
        await ctx.send(f"リロードしました: `{extension}`")
    except Exception as e:
        await ctx.send(f"リロードに失敗しました: `{extension}`\n```{e}```")


@bot.command(name="reloadall")
@commands.is_owner()
async def reload_all(ctx):
    """すべての Cog をリロードする"""
    failed = []

    for extension in list(bot.extensions.keys()):
        try:
            await bot.reload_extension(extension)
        except Exception as e:
            failed.append((extension, e))

    if failed:
        errors = "\n".join(f"- `{extension}`: `{error}`" for extension, error in failed)
        await ctx.send(f"一部の Cog のリロードに失敗しました。\n{errors}")
    else:
        await ctx.send("すべての Cog をリロードしました。")


async def main():
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN が設定されていません。.env ファイルを確認してください。"
        )
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
