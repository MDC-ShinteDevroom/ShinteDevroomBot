from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import discord
from discord.ext import commands
import yt_dlp


logger = logging.getLogger("bot.music")


# ------------------------------------------------------------
# yt-dlp設定
# ------------------------------------------------------------

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn",
}


# ------------------------------------------------------------
# 再生データ
# ------------------------------------------------------------

@dataclass
class Song:
    title: str
    url: str
    webpage_url: str


# ------------------------------------------------------------
# VCごとのプレイヤー
# ------------------------------------------------------------

class MusicPlayer:

    def __init__(self, bot: commands.Bot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id

        self.queue: list[Song] = []

        self.voice: Optional[discord.VoiceClient] = None

        self.current: Optional[Song] = None

        self.loop = False

        self.play_lock = asyncio.Lock()


    # --------------------------------------------------------
    # yt-dlpで音源情報を取得
    # --------------------------------------------------------

    async def extract(self, query: str) -> Optional[Song]:

        def _extract():
            try:
                with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ytdl:

                    info = ytdl.extract_info(
                        query,
                        download=False,
                    )

                    if "entries" in info:
                        entries = info.get("entries")

                        if not entries:
                            return None

                        info = entries[0]

                    return Song(
                        title=info.get("title", "Unknown"),
                        url=info["url"],
                        webpage_url=info.get(
                            "webpage_url",
                            query,
                        ),
                    )

            except Exception:
                logger.exception(
                    "yt-dlpで音源取得に失敗しました"
                )

                return None

        return await asyncio.to_thread(_extract)


    # --------------------------------------------------------
    # 再生
    # --------------------------------------------------------

    async def play_next(self):

        async with self.play_lock:

            if self.voice is None:
                return

            if not self.voice.is_connected():
                return

            # 既に再生中
            if self.voice.is_playing():
                return

            # キューが空
            if not self.queue:

                if self.loop and self.current:
                    self.queue.append(self.current)

                else:
                    self.current = None
                    return

            self.current = self.queue.pop(0)

            song = self.current

            logger.info(
                "再生開始: %s",
                song.title,
            )

            try:

                source = discord.FFmpegPCMAudio(
                    song.url,
                    **FFMPEG_OPTIONS,
                )

                def after(error):

                    if error:
                        logger.error(
                            "再生エラー: %s",
                            error,
                        )

                    asyncio.run_coroutine_threadsafe(
                        self.play_next(),
                        self.bot.loop,
                    )

                self.voice.play(
                    source,
                    after=after,
                )

            except Exception:

                logger.exception(
                    "音声再生に失敗しました"
                )

                asyncio.create_task(
                    self.play_next()
                )


    # --------------------------------------------------------
    # 停止
    # --------------------------------------------------------

    def stop(self):

        if self.voice is not None:

            if self.voice.is_playing():
                self.voice.stop()


    # --------------------------------------------------------
    # キュー追加
    # --------------------------------------------------------

    def add(self, song: Song):

        self.queue.append(song)


    # --------------------------------------------------------
    # キュー削除
    # --------------------------------------------------------

    def clear(self):

        self.queue.clear()


# ------------------------------------------------------------
# Cog
# ------------------------------------------------------------

class Music(commands.Cog):
    """yt-dlpを使用したVC音楽再生Cog"""

    def __init__(self, bot: commands.Bot):

        self.bot = bot

        # guild_id -> MusicPlayer
        self.players: dict[int, MusicPlayer] = {}


    # --------------------------------------------------------
    # Player取得
    # --------------------------------------------------------

    def get_player(
        self,
        guild_id: int,
    ) -> MusicPlayer:

        if guild_id not in self.players:

            self.players[guild_id] = MusicPlayer(
                self.bot,
                guild_id,
            )

        return self.players[guild_id]


    # --------------------------------------------------------
    # VC参加
    # --------------------------------------------------------

    @commands.command(name="join")
    @commands.guild_only()
    async def join(
        self,
        ctx: commands.Context,
    ):

        if ctx.author.voice is None:

            await ctx.send(
                "先にボイスチャンネルへ参加してください。"
            )

            return

        channel = ctx.author.voice.channel

        player = self.get_player(
            ctx.guild.id
        )

        try:

            if ctx.voice_client is not None:

                if ctx.voice_client.channel != channel:

                    await ctx.voice_client.move_to(
                        channel
                    )

            else:

                player.voice = await channel.connect()

            await ctx.send(
                f" **{channel.name}** に参加しました。"
            )

        except Exception:

            logger.exception(
                "VC参加に失敗しました"
            )

            await ctx.send(
                "VCへの参加に失敗しました。"
            )


    # --------------------------------------------------------
    # 再生
    # --------------------------------------------------------

    @commands.command(name="play")
    @commands.guild_only()
    async def play(
        self,
        ctx: commands.Context,
        *,
        query: str,
    ):

        if ctx.author.voice is None:

            await ctx.send(
                "先にボイスチャンネルへ参加してください。"
            )

            return

        player = self.get_player(
            ctx.guild.id
        )

        # VCにいなければ自動参加
        if ctx.voice_client is None:

            try:

                player.voice = await (
                    ctx.author.voice.channel.connect()
                )

            except Exception:

                logger.exception(
                    "VC接続失敗"
                )

                await ctx.send(
                    "VCへの接続に失敗しました。"
                )

                return

        else:

            player.voice = ctx.voice_client

            # 別VCなら移動
            if (
                player.voice.channel
                != ctx.author.voice.channel
            ):

                await player.voice.move_to(
                    ctx.author.voice.channel
                )

        await ctx.send(
            "音源を検索しています..."
        )

        song = await player.extract(query)

        if song is None:

            await ctx.send(
                "音源を取得できませんでした。"
            )

            return

        player.add(song)

        await ctx.send(
            f"**{song.title}** をキューに追加しました。"
        )

        if not player.voice.is_playing():

            await player.play_next()


    # --------------------------------------------------------
    # Pause
    # --------------------------------------------------------

    @commands.command(name="pause")
    @commands.guild_only()
    async def pause(
        self,
        ctx: commands.Context,
    ):

        voice = ctx.voice_client

        if voice is None:

            await ctx.send(
                "VCに接続していません。"
            )

            return

        if voice.is_playing():

            voice.pause()

            await ctx.send(
                "一時停止しました。"
            )

        else:

            await ctx.send(
                "現在再生していません。"
            )


    # --------------------------------------------------------
    # Resume
    # --------------------------------------------------------

    @commands.command(name="resume")
    @commands.guild_only()
    async def resume(
        self,
        ctx: commands.Context,
    ):

        voice = ctx.voice_client

        if voice is None:

            await ctx.send(
                "VCに接続していません。"
            )

            return

        if voice.is_paused():

            voice.resume()

            await ctx.send(
                "再生を再開しました。"
            )

        else:

            await ctx.send(
                "一時停止されていません。"
            )


    # --------------------------------------------------------
    # Skip
    # --------------------------------------------------------

    @commands.command(name="skip")
    @commands.guild_only()
    async def skip(
        self,
        ctx: commands.Context,
    ):

        voice = ctx.voice_client

        if voice is None:

            await ctx.send(
                "VCに接続していません。"
            )

            return

        if voice.is_playing() or voice.is_paused():

            voice.stop()

            await ctx.send(
                "スキップしました。"
            )

        else:

            await ctx.send(
                "現在再生していません。"
            )


    # --------------------------------------------------------
    # Stop
    # --------------------------------------------------------

    @commands.command(name="stop")
    @commands.guild_only()
    async def stop(
        self,
        ctx: commands.Context,
    ):

        player = self.get_player(
            ctx.guild.id
        )

        player.clear()
        player.stop()

        await ctx.send(
            "再生を停止してキューを削除しました。"
        )


    # --------------------------------------------------------
    # Queue
    # --------------------------------------------------------

    @commands.command(name="queue")
    @commands.guild_only()
    async def queue(
        self,
        ctx: commands.Context,
    ):

        player = self.get_player(
            ctx.guild.id
        )

        if not player.queue:

            await ctx.send(
                "キューは空です。"
            )

            return

        lines = []

        for i, song in enumerate(
            player.queue,
            start=1,
        ):

            lines.append(
                f"`{i}.` {song.title}"
            )

        text = "\n".join(lines)

        await ctx.send(
            "**再生キュー**\n" + text
        )


    # --------------------------------------------------------
    # Now Playing
    # --------------------------------------------------------

    @commands.command(name="nowplaying")
    @commands.guild_only()
    async def nowplaying(
        self,
        ctx: commands.Context,
    ):

        player = self.get_player(
            ctx.guild.id
        )

        if player.current is None:

            await ctx.send(
                "現在何も再生していません。"
            )

            return

        await ctx.send(
            f"**現在再生中**\n"
            f"{player.current.title}"
        )


    # --------------------------------------------------------
    # Leave
    # --------------------------------------------------------

    @commands.command(name="leave")
    @commands.guild_only()
    async def leave(
        self,
        ctx: commands.Context,
    ):

        voice = ctx.voice_client

        if voice is None:

            await ctx.send(
                "VCに接続していません。"
            )

            return

        player = self.get_player(
            ctx.guild.id
        )

        player.clear()
        player.current = None

        await voice.disconnect()

        player.voice = None

        await ctx.send(
            "VCから退出しました。"
        )


    # --------------------------------------------------------
    # エラーハンドリング
    # --------------------------------------------------------

    @play.error
    async def play_error(
        self,
        ctx: commands.Context,
        error,
    ):

        if isinstance(
            error,
            commands.MissingRequiredArgument,
        ):

            await ctx.send(
                "使い方: `!play URLまたは検索ワード`"
            )

        else:

            logger.exception(
                "playコマンドエラー",
                exc_info=error,
            )

            await ctx.send(
                "再生中にエラーが発生しました。"
            )


# ------------------------------------------------------------
# setup
# ------------------------------------------------------------

async def setup(
    bot: commands.Bot,
):

    await bot.add_cog(
        Music(bot)
    )
