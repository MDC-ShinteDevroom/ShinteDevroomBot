import json
import logging
from pathlib import Path

import discord
from discord.ext import commands

logger = logging.getLogger("bot.forumlog")

# json作る。
DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "forumlog.json"


class ForumLog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config: dict = self._load()

    def _load(self) -> dict:
        if DATA_PATH.exists():
            try:
                return json.loads(DATA_PATH.read_text(encoding="utf-8"))
            except Exception:
                logger.exception("forumlog の設定読み込みに失敗しました")
        return {}

    def _save(self) -> None:
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        DATA_PATH.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _conf(self, guild_id: int) -> dict:
        return self.config.setdefault(
            str(guild_id), {"log_channel": None, "forums": []}
        )

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if thread.guild is None:
            return

        conf = self.config.get(str(thread.guild.id))
        if not conf:
            return
        if thread.parent_id not in conf.get("forums", []):
            return

        channel_id = conf.get("log_channel")
        if not channel_id:
            return

        channel = thread.guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await thread.guild.fetch_channel(channel_id)
            except discord.HTTPException:
                logger.warning(f"通知先チャンネルが見つかりません: {channel_id}")
                return

        if not isinstance(channel, discord.abc.Messageable):
            logger.warning(
                f"通知先チャンネルはメッセージを送信できません: {channel_id}"
            )
            return

        owner = thread.owner
        if owner is None and thread.owner_id:
            try:
                owner = await thread.guild.fetch_member(thread.owner_id)
            except discord.HTTPException:
                owner = None

        author_text = owner.mention if owner else f"<@{thread.owner_id}>"

        embed = discord.Embed(
            title=thread.name,
            url=thread.jump_url,
            color=discord.Color.blurple(),
            timestamp=thread.created_at or discord.utils.utcnow(),
        )
        embed.add_field(name="フォーラム", value=f"<#{thread.parent_id}>", inline=True)
        embed.add_field(name="作成者", value=author_text, inline=True)
        embed.add_field(name="URL", value=thread.jump_url, inline=False)
        if owner:
            embed.set_author(name=str(owner), icon_url=owner.display_avatar.url)

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning(f"通知先チャンネルへの送信権限がありません: {channel_id}")

    # うんこ

    @commands.group(name="forumlog", invoke_without_command=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def forumlog(self, ctx: commands.Context):
        await self.show(ctx)

    @forumlog.command(name="set")
    async def set_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        assert ctx.guild is not None

        self._conf(ctx.guild.id)["log_channel"] = channel.id
        self._save()
        await ctx.send(f"通知先を {channel.mention} に設定しました。")

    @forumlog.command(name="add")
    async def add_forum(self, ctx: commands.Context, forum: discord.ForumChannel):
        assert ctx.guild is not None

        forums = self._conf(ctx.guild.id)["forums"]
        if forum.id in forums:
            await ctx.send(f"{forum.mention} は既に監視対象です。")
            return
        forums.append(forum.id)
        self._save()
        await ctx.send(f"{forum.mention} を監視対象に追加しました。")

    @forumlog.command(name="remove")
    async def remove_forum(self, ctx: commands.Context, forum: discord.ForumChannel):
        assert ctx.guild is not None

        forums = self._conf(ctx.guild.id)["forums"]
        if forum.id not in forums:
            await ctx.send(f"{forum.mention} は監視対象ではありません。")
            return
        forums.remove(forum.id)
        self._save()
        await ctx.send(f"{forum.mention} を監視対象から外しました。")

    @forumlog.command(name="show")
    async def show(self, ctx: commands.Context):
        """現在の設定を表示する"""
        assert ctx.guild is not None

        conf = self.config.get(str(ctx.guild.id))
        if not conf:
            await ctx.send(
                "まだ設定されていません。`!forumlog set #チャンネル` から設定してください。"
            )
            return

        ch = f"<#{conf['log_channel']}>" if conf.get("log_channel") else "未設定"
        forums = conf.get("forums", [])
        forum_text = "\n".join(f"・<#{fid}>" for fid in forums) if forums else "なし"
        await ctx.send(f"**通知先**: {ch}\n**監視中のフォーラム**:\n{forum_text}")

    @forumlog.command(name="off")
    async def disable(self, ctx: commands.Context):
        assert ctx.guild is not None

        self.config.pop(str(ctx.guild.id), None)
        self._save()
        await ctx.send("このサーバーの設定を削除しました。")


async def setup(bot: commands.Bot):
    await bot.add_cog(ForumLog(bot))
