from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import discord
from discord.ext import commands

logger = logging.getLogger("bot.katm")

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "katm.json"
LOCKS_PATH = Path(__file__).resolve().parent.parent / "data" / "katm_locks.json"

# このコマンドで使うプレフィックス（bot.py の get_prefix と合わせる）
KATM_PREFIX = ","

# BAN までの待機時間（秒）
DELAY_SECONDS = 10 * 60

# BAN 後、解除を保護する時間（秒）
UNBAN_LOCK_SECONDS = 10 * 60 * 60


class Katm(commands.Cog):
    """実行から10分後に対象ユーザーをサイレントBANする"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {"guild_id:user_id": {guild_id, user_id, ban_at, channel_id, moderator_id}}
        self.pending: dict[str, dict] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        # {"guild_id:user_id": {guild_id, user_id, unlock_at, channel_id}}
        self.locks: dict[str, dict] = {}

    # ---------- ライフサイクル ----------

    async def cog_load(self):
        self.pending = self._load()
        for key in list(self.pending):
            self._schedule(key)

        self.locks = self._load_locks()
        # 期限切れの記録は起動時に掃除しておく
        now = time.time()
        expired = [k for k, v in self.locks.items() if v["unlock_at"] <= now]
        if expired:
            for k in expired:
                self.locks.pop(k, None)
            self._save_locks()

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
                logger.exception("katm.json の読み込みに失敗しました")
        return {}

    def _save(self) -> None:
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        DATA_PATH.write_text(
            json.dumps(self.pending, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load_locks(self) -> dict:
        if LOCKS_PATH.exists():
            try:
                return json.loads(LOCKS_PATH.read_text(encoding="utf-8"))
            except Exception:
                logger.exception("katm_locks.json の読み込みに失敗しました")
        return {}

    def _save_locks(self) -> None:
        LOCKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCKS_PATH.write_text(
            json.dumps(self.locks, ensure_ascii=False, indent=2), encoding="utf-8"
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
            logger.exception(f"katm の実行中にエラーが発生しました: {key}")
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

        # まだサーバーに在籍している場合は、実行直前に役職の順位を確認する
        member = guild.get_member(user_id)
        if member is not None:
            me = guild.me
            if member.id == guild.owner_id or member.guild_permissions.administrator:
                result = f"<@{user_id}> (ID: {user_id}) はBANできません（管理者権限またはオーナーのため）。予約を取り消しました。"
                self.pending.pop(key, None)
                self._save()
                await self._notify(guild, entry, result)
                return
            if member.top_role >= me.top_role:
                result = f"<@{user_id}> (ID: {user_id}) はBANできません（Botより役職が上位のため）。予約を取り消しました。"
                self.pending.pop(key, None)
                self._save()
                await self._notify(guild, entry, result)
                return

        try:
            await guild.ban(
                discord.Object(id=user_id),
                reason="katm",
                delete_message_seconds=0,  # メッセージは削除しない
            )
            result = f"<@{user_id}> (ID: {user_id}) をBANしました。"

            # 一定時間、解除されても即座に再BANするよう記録する
            unlock_at = time.time() + UNBAN_LOCK_SECONDS
            self.locks[key] = {
                "guild_id": guild.id,
                "user_id": user_id,
                "unlock_at": unlock_at,
                "channel_id": entry.get("channel_id"),
            }
            self._save_locks()
        except discord.Forbidden:
            result = f"<@{user_id}> (ID: {user_id}) のBANに失敗しました。Botの権限を確認してください。"
        except discord.HTTPException:
            logger.exception("BAN に失敗しました")
            result = f"<@{user_id}> (ID: {user_id}) のBANに失敗しました。"

        self.pending.pop(key, None)
        self._save()
        await self._notify(guild, entry, result)

    async def _notify(self, guild: discord.Guild, entry: dict, text: str) -> None:
        channel = guild.get_channel_or_thread(entry.get("channel_id") or 0)
        if channel:
            try:
                await channel.send(
                    text, allowed_mentions=discord.AllowedMentions.none()
                )
            except discord.HTTPException:
                pass

    # ---------- BAN解除の保護 ----------

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        key = f"{guild.id}:{user.id}"
        lock = self.locks.get(key)
        if not lock:
            return

        if time.time() >= lock["unlock_at"]:
            # 保護期間は終了しているので、記録だけ削除する
            self.locks.pop(key, None)
            self._save_locks()
            return

        # 保護期間中に解除されたので、即座に再BANする
        try:
            await guild.ban(
                discord.Object(id=user.id),
                reason="katm: 解除保護期間中のため再BAN",
                delete_message_seconds=0,
            )
            unlock_ts = int(lock["unlock_at"])
            text = (
                f"<@{user.id}> (ID: {user.id}) のBAN解除を検知しましたが、"
                f"保護期間中のため再度BANしました。解除可能になる時刻: <t:{unlock_ts}:f>"
            )
        except discord.Forbidden:
            text = f"<@{user.id}> (ID: {user.id}) の再BANに失敗しました。Botの権限を確認してください。"
        except discord.HTTPException:
            logger.exception("再BAN に失敗しました")
            text = f"<@{user.id}> (ID: {user.id}) の再BANに失敗しました。"

        channel = guild.get_channel_or_thread(lock.get("channel_id") or 0)
        if channel:
            try:
                await channel.send(
                    text, allowed_mentions=discord.AllowedMentions.none()
                )
            except discord.HTTPException:
                pass

    # ---------- コマンド ----------

    @commands.command(name="katm")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def katm(self, ctx: commands.Context, user: discord.User):
        """10分後にサイレントBANを予約する（例: ,katm @user / ,katm 123456789012345678）"""
        # , 以外（!katm など）では動作させない
        if ctx.prefix != KATM_PREFIX:
            return

        guild = ctx.guild

        if user.id == ctx.author.id:
            await ctx.send("自分自身は対象にできません。")
            return
        if user.id == self.bot.user.id:
            await ctx.send("Bot自身は対象にできません。")
            return
        if user.id == guild.owner_id:
            await ctx.send("サーバーオーナーは対象にできません。")
            return

        member = guild.get_member(user.id)
        if member is not None:
            if member.guild_permissions.administrator:
                await ctx.send("管理者権限を持つメンバーは対象にできません。")
                return
            if ctx.author.id != guild.owner_id and member.top_role >= ctx.author.top_role:
                await ctx.send("自分より上位（または同じ）役職のメンバーは対象にできません。")
                return
            if member.top_role >= guild.me.top_role:
                await ctx.send("Botの役職より上位（または同じ）のメンバーは対象にできません。")
                return

        if not guild.me.guild_permissions.ban_members:
            await ctx.send("Botに「メンバーをBAN」の権限が必要です。")
            return

        key = f"{guild.id}:{user.id}"
        if key in self.pending:
            await ctx.send(
                f"{user.mention} は既に予約済みです。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        ban_at = time.time() + DELAY_SECONDS
        self.pending[key] = {
            "guild_id": guild.id,
            "user_id": user.id,
            "ban_at": ban_at,
            "channel_id": ctx.channel.id,
            "moderator_id": ctx.author.id,
        }
        self._save()
        self._schedule(key)

        ts = int(ban_at)
        await ctx.send(f"<t:{ts}:R>", allowed_mentions=discord.AllowedMentions.none())

async def setup(bot: commands.Bot):
    await bot.add_cog(Katm(bot))
