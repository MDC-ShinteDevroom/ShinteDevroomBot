import logging
from datetime import timedelta

import discord
from discord.ext import commands

logger = logging.getLogger("bot.purge")

# このコマンドで使うプレフィックス（bot.py の get_prefix と合わせる）
PURGE_PREFIX = "%"

# 一度に削除できる最大件数
MAX_PURGE = 1000

# ユーザーを指定した場合に、そのユーザーのメッセージを探してさかのぼる最大件数
SCAN_LIMIT = 1000

# Discord の一括削除は14日より古いメッセージには使えない（余裕を持たせて13日23時間）
BULK_MAX_AGE = timedelta(days=13, hours=23)


class Purge(commands.Cog):
    """チャンネルのメッセージを一括削除する"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="purge")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def purge(
        self,
        ctx: commands.Context,
        number: int,
        user: discord.User = None,
    ):
        """メッセージを削除する（例: %purge 10 / %purge 10 @user / %purge 10 123456789012345678）"""
        # % 以外（!purge など）では動作させない
        if ctx.prefix != PURGE_PREFIX:
            return

        if number < 1 or number > MAX_PURGE:
            await ctx.send(f"削除件数は 1〜{MAX_PURGE} の範囲で指定してください。")
            return

        perms = ctx.channel.permissions_for(ctx.guild.me)
        if not (perms.manage_messages and perms.read_message_history):
            await ctx.send(
                "Botに「メッセージの管理」と「メッセージ履歴を読む」の権限が必要です。"
            )
            return

        # コマンド自身のメッセージは件数に含めず、先に消す
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

        # 削除対象を集める
        # ユーザー指定なし: 直近 number 件 / あり: そのユーザーの直近 number 件
        limit = SCAN_LIMIT if user is not None else number
        targets: list[discord.Message] = []
        async for msg in ctx.channel.history(limit=limit, before=ctx.message):
            if user is not None and msg.author.id != user.id:
                continue
            targets.append(msg)
            if len(targets) >= number:
                break

        if not targets:
            await ctx.send(
                "削除対象のメッセージが見つかりませんでした。", delete_after=5
            )
            return

        # 一括削除できる（14日以内）ものと、個別に消すものに分ける
        cutoff = discord.utils.utcnow() - BULK_MAX_AGE
        recent = [m for m in targets if m.created_at > cutoff]
        old = [m for m in targets if m.created_at <= cutoff]

        deleted = 0
        try:
            for i in range(0, len(recent), 100):
                chunk = recent[i : i + 100]
                if len(chunk) == 1:
                    await chunk[0].delete()
                else:
                    await ctx.channel.delete_messages(chunk)
                deleted += len(chunk)

            for msg in old:
                try:
                    await msg.delete()
                    deleted += 1
                except discord.NotFound:
                    pass
        except discord.Forbidden:
            await ctx.send("メッセージを削除する権限がありません。", delete_after=10)
            return
        except discord.HTTPException:
            logger.exception("メッセージの削除に失敗しました")
            await ctx.send(
                f"削除中にエラーが発生しました。（{deleted}件は削除済み）",
                delete_after=10,
            )
            return

        text = f"{deleted}件のメッセージを削除しました。"
        if user is not None:
            text = f"{user.mention} のメッセージを{deleted}件削除しました。"
            if deleted < number:
                text += (
                    f"\n（直近{SCAN_LIMIT}件の範囲では、該当が{deleted}件のみでした）"
                )

        await ctx.send(
            text,
            delete_after=5,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Purge(bot))
