from __future__ import annotations

import logging
import random
from pathlib import Path

import discord
from discord.ext import commands

logger = logging.getLogger("bot.random_photo")

# 画像を保存するディレクトリ（プロジェクトルート直下の photo/）
PHOTO_DIR = Path(__file__).parent.parent / "photo"


class RandomPhoto(commands.Cog):
    """photo/ からランダムでPNG画像を送信するCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="gr3nja")
    async def gr3nja(self, ctx: commands.Context):
        """photo/ に保存されたPNGファイルをランダムで送信する（誰でも実行可能）"""
        if not PHOTO_DIR.is_dir():
            logger.warning("画像ディレクトリが見つかりません: %s", PHOTO_DIR)
            await ctx.send("画像を送信できませんでした。")
            return

        png_files = [
            p for p in PHOTO_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".png"
        ]

        if not png_files:
            logger.warning("photo/ にPNGファイルが見つかりません")
            await ctx.send("送信できる画像がありません。")
            return

        chosen = random.choice(png_files)

        try:
            await ctx.send(file=discord.File(chosen))
        except discord.HTTPException:
            logger.exception("画像の送信に失敗しました: %s", chosen)
            await ctx.send("画像の送信に失敗しました。")


async def setup(bot: commands.Bot):
    await bot.add_cog(RandomPhoto(bot))
