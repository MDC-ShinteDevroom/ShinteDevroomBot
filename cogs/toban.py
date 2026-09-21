from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import timedelta
from pathlib import Path

import discord
from discord.ext import commands

logger = logging.getLogger("bot.toban")

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "toban.json"

# このコマンドで使うプレフィックス（bot.py の get_prefix と合わせる）
TOBAN_PREFIX = "%"

# Discord のタイムアウト上限は28日
MAX_TIMEOUT = timedelta(days=28)

TIME_PATTERN = re.compile(
    r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", re.IGNORECASE
)


def parse_duration(text: str) -> timedelta | None:
    """'10m' '1h30m' '2d' '90s' などを timedelta に変換する"""
    m = TIME_PATTERN.match(text.strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    td = timedelta(days=d, hours=h, minutes=mi, seconds=s)
    return td if td.total_seconds() > 0 else None


class Toban(commands.Cog):
    """指定時間タイムアウトし、終了後にサイレントBANする"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {"guild_id:user_id": {guild_id, user_id, ban_at, reason, channel_id, moderator_id}}
        self.pending: dict[str, dict] = {}
        self.tasks: dict[str, asyncio.Task] = {}


    async def cog_load(self):
        self.pending = self._load()
        for key in list(self.pending):
            self._schedule(key)

    async def cog_unload(self):
        for task in self.tasks.values():
            task.cancel()
        self.tasks.clear()

    # ---------- 保存 ----------

    def _load(self) -> dict:
        if DATA_PATH.exists():
            try:
                return json.loads(DATA_PATH.read_text(encoding="utf-8"))
            except Exception:
                logger.exception("toban.json の読み込みに失敗しました")
        return {}

    def _save(self) -> None:
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        DATA_PATH.write_text(
            json.dumps(self.pending, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- BAN の予約と実行 ----------

    def _schedule(self, key: str) -> None:
        old = self.tasks.pop(key, None)
        if old:
            old.cancel()
        self.tasks[key] = asyncio.create_task(self._run(key))

    async def _run(self, key: str) -> None:
        try:
            await self.bot.wait_until_ready()
            entry = self.pending.get(key)
            if not entry:
                return
            delay = entry["ban_at"] - time.time()
            if delay > 0:
                await asyncio.sleep(delay)
            await self._execute_ban(key)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f"toban の実行中にエラーが発生しました: {key}")
        finally:
            if self.tasks.get(key) is asyncio.current_task():
                self.tasks.pop(key, None)

    async def _execute_ban(self, key: str) -> None:
        entry = self.pending.get(key)
        if not entry:
            return

        guild = self.bot.get_guild(entry["guild_id"])
        if guild is None:
            logger.warning(f"サーバーが見つからないため予約を破棄します: {key}")
            self.pending.pop(key, None)
            self._save()
            return

        user_id = entry["user_id"]
        try:
            await guild.ban(
                discord.Object(id=user_id),
                reason=f"toban: {entry['reason']}"[:512],
                delete_message_seconds=0,  # メッセージは削除しない
            )
            result = f"<@{user_id}> (ID: {user_id}) をBANしました。\n理由: {entry['reason']}"
        except discord.Forbidden:
            result = f"<@{user_id}> (ID: {user_id}) のBANに失敗しました。Botの権限または役職の順位を確認してください。"
        except discord.HTTPException:
            logger.exception("BAN に失敗しました")
            result = f"<@{user_id}> (ID: {user_id}) のBANに失敗しました。"

        self.pending.pop(key, None)
        self._save()

        channel = guild.get_channel_or_thread(entry.get("channel_id") or 0)
        if channel:
            try:
                await channel.send(
                    result, allowed_mentions=discord.AllowedMentions.none()
                )
            except discord.HTTPException:
                pass


    @commands.command(name="toban")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def toban(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: str,
        *,
        reason: str,
    ):
        """指定時間タイムアウト後にBANする（例: %toban @user 1h30m 荒らし行為）"""
        # % 以外（!toban など）では動作させない
        if ctx.prefix != TOBAN_PREFIX:
            return

        td = parse_duration(duration)
        if td is None:
            await ctx.send("時間の形式が正しくありません。例: `30m` `1h30m` `2d` `90s`")
            return
        if td > MAX_TIMEOUT:
            await ctx.send("タイムアウトの上限は28日です。")
            return

        guild = ctx.guild
        me = guild.me

        # 対象のチェック
        if member.id == ctx.author.id:
            await ctx.send("自分自身は対象にできません。")
            return
        if member.id == self.bot.user.id:
            await ctx.send("Bot自身は対象にできません。")
            return
        if member.id == guild.owner_id or member.guild_permissions.administrator:
            await ctx.send("管理者権限を持つメンバーは対象にできません。")
            return
        if ctx.author.id != guild.owner_id and member.top_role >= ctx.author.top_role:
            await ctx.send("自分より上位（または同じ）役職のメンバーは対象にできません。")
            return
        if member.top_role >= me.top_role:
            await ctx.send("Botの役職より上位（または同じ）のメンバーは対象にできません。")
            return

        perms = me.guild_permissions
        if not (perms.moderate_members and perms.ban_members):
            await ctx.send("Botに「メンバーをタイムアウト」と「メンバーをBAN」の権限が必要です。")
            return

        key = f"{guild.id}:{member.id}"
        if key in self.pending:
            await ctx.send(
                f"{member.mention} は既に予約済みです。取り消す場合は `%tobancancel` を使ってください。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        # タイムアウト
        try:
            await member.timeout(
                td, reason=f"toban: {reason} (実行者: {ctx.author})"[:512]
            )
        except discord.Forbidden:
            await ctx.send("タイムアウトに失敗しました。Botの権限を確認してください。")
            return
        except discord.HTTPException:
            logger.exception("タイムアウトに失敗しました")
            await ctx.send("タイムアウトに失敗しました。")
            return

        # BAN を予約（再起動しても残る）
        ban_at = time.time() + td.total_seconds()
        self.pending[key] = {
            "guild_id": guild.id,
            "user_id": member.id,
            "ban_at": ban_at,
            "reason": reason,
            "channel_id": ctx.channel.id,
            "moderator_id": ctx.author.id,
        }
        self._save()
        self._schedule(key)

        ts = int(ban_at)
        await ctx.send(
            f"{member.mention} をタイムアウトしました。\n"
            f"BAN予定: <t:{ts}:F>（<t:{ts}:R>）\n"
            f"理由: {reason}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(name="tobancancel")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def tobancancel(self, ctx: commands.Context, user: discord.User):
        """予約中のBANを取り消し、タイムアウトも解除する"""
        # % 以外（!tobancancel など）では動作させない
        if ctx.prefix != TOBAN_PREFIX:
            return

        key = f"{ctx.guild.id}:{user.id}"
        if key not in self.pending:
            await ctx.send("そのユーザーのBAN予約はありません。")
            return

        task = self.tasks.pop(key, None)
        if task:
            task.cancel()
        self.pending.pop(key, None)
        self._save()

        member = ctx.guild.get_member(user.id)
        if member:
            try:
                await member.timeout(None, reason=f"toban取り消し (実行者: {ctx.author})")
            except discord.HTTPException:
                pass

        await ctx.send(
            f"{user.mention} のBAN予約を取り消しました。",
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Toban(bot))
