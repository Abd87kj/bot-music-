import os
import io
import json
import time
import asyncio
import discord
import aiohttp
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
import arabic_reshaper
from bidi.algorithm import get_display
import yt_dlp
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "0"))
FAVORITES_PATH = os.getenv("FAVORITES_PATH", "favorites.json")
FONT_PATH = os.getenv("FONT_PATH", "assets/fonts/arabic.ttf")
FONT_BOLD_PATH = os.getenv("FONT_BOLD_PATH", FONT_PATH)

DEFAULT_SEEK_SECONDS = 10
CARD_UPDATE_INTERVAL = 10  # ثواني

# === التعديل الوحيد المهم: البحث الافتراضي صار من ساوند كلاود بدل يوتيوب ===
# scsearch1 = يبحث بساوند كلاود ويرجع أول نتيجة (بدل ytsearch1)
# لو حد لصق رابط ساوند كلاود مباشر، بيشتغل عادي برضو لأن yt-dlp يتعرف على الرابط تلقائياً
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "default_search": "scsearch1",
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
}

FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn"

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!!__never__!!", intents=intents, help_command=None)

guild_states = {}


def get_state(guild_id: int):
    if guild_id not in guild_states:
        guild_states[guild_id] = {
            "queue": [],
            "current": None,
            "repeat": False,
            "voice_client": None,
            "start_time": None,
            "paused_at": None,
            "seek_offset": 0,
            "text_channel": None,
            "now_message": None,
            "manual_seek": False,
            "card_base": None,
            "volume": 1.0,
        }
    return guild_states[guild_id]


def in_target_channel(member: discord.Member) -> bool:
    if VOICE_CHANNEL_ID == 0:
        return True
    return bool(
        member.voice and member.voice.channel and member.voice.channel.id == VOICE_CHANNEL_ID
    )


def _load_favorites():
    if os.path.exists(FAVORITES_PATH):
        try:
            with open(FAVORITES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


favorites_data = _load_favorites()


def _save_favorites():
    try:
        with open(FAVORITES_PATH, "w", encoding="utf-8") as f:
            json.dump(favorites_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"تعذر حفظ المفضلة: {e}")


def add_favorite(user_id: int, song: dict) -> bool:
    key = str(user_id)
    lst = favorites_data.setdefault(key, [])
    for item in lst:
        if item.get("webpage_url") == song.get("webpage_url"):
            return False
    lst.append(
        {
            "title": song["title"],
            "webpage_url": song["webpage_url"],
            "thumbnail": song.get("thumbnail"),
        }
    )
    _save_favorites()
    return True


async def extract_song(query: str):
    loop = asyncio.get_event_loop()

    def _extract():
        info = ytdl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        return info

    info = await loop.run_in_executor(None, _extract)
    return {
        "title": info.get("title", "بدون عنوان"),
        "url": info.get("url"),
        "webpage_url": info.get("webpage_url"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration", 0),
        "uploader": info.get("uploader", "غير معروف"),
    }


def make_source(url: str, seek_seconds: int = 0, volume: float = 1.0):
    before = FFMPEG_BEFORE_OPTIONS
    if seek_seconds > 0:
        before = f"-ss {seek_seconds} " + before
    audio = discord.FFmpegPCMAudio(url, before_options=before, options=FFMPEG_OPTIONS)
    return discord.PCMVolumeTransformer(audio, volume=volume)


async def fetch_thumbnail_bytes(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            return await resp.read()


CARD_WIDTH = 1000
CARD_HEIGHT = 320
ALBUM_SIZE = 260
PADDING = 30


def _reshape(text: str) -> str:
    try:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except Exception:
        return text


def _load_font(size: int, bold: bool = False):
    path = FONT_BOLD_PATH if bold else FONT_PATH
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _fmt_time(seconds) -> str:
    seconds = max(int(seconds), 0)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def build_base_card(thumbnail_bytes: bytes, title: str, artist: str) -> Image.Image:
    thumb = Image.open(io.BytesIO(thumbnail_bytes)).convert("RGB")

    bg = ImageOps.fit(thumb, (CARD_WIDTH, CARD_HEIGHT), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(35))
    overlay = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0))
    card = Image.blend(bg, overlay, 0.55)

    draw = ImageDraw.Draw(card)

    album = ImageOps.fit(thumb, (ALBUM_SIZE, ALBUM_SIZE), Image.LANCZOS)
    mask = Image.new("L", (ALBUM_SIZE, ALBUM_SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, ALBUM_SIZE, ALBUM_SIZE], radius=24, fill=255)
    album_y = (CARD_HEIGHT - ALBUM_SIZE) // 2
    card.paste(album, (PADDING, album_y), mask)

    text_x = PADDING * 2 + ALBUM_SIZE
    title_font = _load_font(40, bold=True)
    artist_font = _load_font(26)

    display_title = title if len(title) <= 46 else title[:44] + "…"
    draw.text((text_x, 50), _reshape(display_title), font=title_font, fill="white")
    draw.text((text_x, 105), _reshape(artist), font=artist_font, fill=(190, 190, 190))

    return card


def build_placeholder_card(title: str, artist: str) -> Image.Image:
    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), (30, 30, 40))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle(
        [PADDING, (CARD_HEIGHT - ALBUM_SIZE) // 2, PADDING + ALBUM_SIZE, (CARD_HEIGHT + ALBUM_SIZE) // 2],
        radius=24,
        fill=(60, 60, 80),
    )
    text_x = PADDING * 2 + ALBUM_SIZE
    title_font = _load_font(40, bold=True)
    artist_font = _load_font(26)
    display_title = title if len(title) <= 46 else title[:44] + "…"
    draw.text((text_x, 50), _reshape(display_title), font=title_font, fill="white")
    draw.text((text_x, 105), _reshape(artist), font=artist_font, fill=(190, 190, 190))
    return card


def render_card_frame(card_base: Image.Image, elapsed: float, duration: float, paused: bool) -> io.BytesIO:
    card = card_base.copy()
    draw = ImageDraw.Draw(card)

    text_x = PADDING * 2 + ALBUM_SIZE
    bar_y = CARD_HEIGHT - 90
    bar_x1 = text_x
    bar_x2 = CARD_WIDTH - PADDING
    bar_width = bar_x2 - bar_x1

    safe_duration = max(duration, 1)
    ratio = min(max(elapsed / safe_duration, 0), 1.0)

    draw.line([(bar_x1, bar_y), (bar_x2, bar_y)], fill=(255, 255, 255, 90), width=4)
    progress_x = bar_x1 + int(bar_width * ratio)
    draw.line([(bar_x1, bar_y), (progress_x, bar_y)], fill="white", width=4)
    draw.ellipse([progress_x - 7, bar_y - 7, progress_x + 7, bar_y + 7], fill="white")

    time_font = _load_font(20)
    draw.text((bar_x1, bar_y + 15), _fmt_time(elapsed), font=time_font, fill=(210, 210, 210))
    dur_text = _fmt_time(duration)
    try:
        dur_w = draw.textlength(dur_text, font=time_font)
    except Exception:
        dur_w = len(dur_text) * 11
    draw.text((bar_x2 - dur_w, bar_y + 15), dur_text, font=time_font, fill=(210, 210, 210))

    icon_y = 170
    icon_x = text_x + (bar_width // 2)
    if paused:
        draw.polygon(
            [(icon_x - 12, icon_y - 16), (icon_x - 12, icon_y + 16), (icon_x + 16, icon_y)], fill="white"
        )
    else:
        draw.rectangle([icon_x - 14, icon_y - 16, icon_x - 4, icon_y + 16], fill="white")
        draw.rectangle([icon_x + 4, icon_y - 16, icon_x + 14, icon_y + 16], fill="white")

    buf = io.BytesIO()
    card.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def refresh_card_now(guild: discord.Guild, paused: bool):
    state = get_state(guild.id)
    base = state.get("card_base")
    msg = state.get("now_message")
    song = state.get("current")
    if not (base and msg and song):
        return
    elapsed = 0
    if state.get("start_time"):
        elapsed = time.time() - state["start_time"] + state["seek_offset"]
    duration = song.get("duration") or 0
    try:
        frame = render_card_frame(base, elapsed, duration, paused=paused)
        file = discord.File(frame, filename="now_playing.png")
        embed = msg.embeds[0] if msg.embeds else discord.Embed(title="🎶 يشتغل الآن", color=discord.Color.blurple())
        embed.set_image(url="attachment://now_playing.png")
        await msg.edit(embed=embed, attachments=[file])
    except Exception as e:
        print(f"تعذر تحديث البطاقة: {e}")


@tasks.loop(seconds=CARD_UPDATE_INTERVAL)
async def update_now_playing_cards():
    for guild_id, state in list(guild_states.items()):
        vc = state.get("voice_client")
        song = state.get("current")
        msg = state.get("now_message")
        base = state.get("card_base")
        if not (vc and song and msg and base):
            continue
        if not vc.is_playing():
            continue
        elapsed = time.time() - state["start_time"] + state["seek_offset"]
        duration = song.get("duration") or 0
        if duration and elapsed >= duration:
            continue
        try:
            frame = render_card_frame(base, elapsed, duration, paused=False)
            file = discord.File(frame, filename="now_playing.png")
            embed = msg.embeds[0] if msg.embeds else discord.Embed(title="🎶 يشتغل الآن", color=discord.Color.blurple())
            embed.set_image(url="attachment://now_playing.png")
            await msg.edit(embed=embed, attachments=[file])
        except discord.NotFound:
            state["now_message"] = None
        except Exception as e:
            print(f"تعذر تحديث بطاقة الأغنية: {e}")


class MusicControls(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    async def _check_room(self, interaction: discord.Interaction) -> bool:
        if not in_target_channel(interaction.user):
            await interaction.response.send_message(
                "لازم تكون جالس بنفس الروم الصوتي حق هذا البوت عشان تتحكم فيه.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, row=0)
    async def repeat_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_room(interaction):
            return
        state = get_state(self.guild_id)
        state["repeat"] = not state["repeat"]
        status = "✅ تم تفعيل التكرار" if state["repeat"] else "❌ تم إيقاف التكرار"
        await interaction.response.send_message(status, ephemeral=True)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, row=0)
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_room(interaction):
            return
        state = get_state(self.guild_id)
        state["volume"] = round(min(state.get("volume", 1.0) + 0.1, 2.0), 2)
        vc = state["voice_client"]
        if vc and vc.source is not None and hasattr(vc.source, "volume"):
            vc.source.volume = state["volume"]
        await interaction.response.send_message(f"🔊 مستوى الصوت: {int(state['volume']*100)}%", ephemeral=True)

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary, row=0)
    async def play_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_room(interaction):
            return
        state = get_state(self.guild_id)
        vc = state["voice_client"]
        guild = interaction.guild
        if vc is None:
            await interaction.response.send_message("البوت مو متصل بروم صوتي.", ephemeral=True)
            return
        if vc.is_playing():
            vc.pause()
            state["paused_at"] = time.time()
            await interaction.response.send_message("⏸️ تم الإيقاف المؤقت.", ephemeral=True)
            await refresh_card_now(guild, paused=True)
        elif vc.is_paused():
            vc.resume()
            if state.get("paused_at"):
                state["start_time"] += time.time() - state["paused_at"]
                state["paused_at"] = None
            await interaction.response.send_message("▶️ تم الاستكمال.", ephemeral=True)
            await refresh_card_now(guild, paused=False)
        else:
            await interaction.response.send_message("ما فيه شي يشتغل حالياً.", ephemeral=True)

    @discord.ui.button(emoji="🔉", style=discord.ButtonStyle.secondary, row=0)
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_room(interaction):
            return
        state = get_state(self.guild_id)
        state["volume"] = round(max(state.get("volume", 1.0) - 0.1, 0.0), 2)
        vc = state["voice_client"]
        if vc and vc.source is not None and hasattr(vc.source, "volume"):
            vc.source.volume = state["volume"]
        await interaction.response.send_message(f"🔉 مستوى الصوت: {int(state['volume']*100)}%", ephemeral=True)

    @discord.ui.button(emoji="❤️", style=discord.ButtonStyle.secondary, row=0)
    async def favorite_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        song = state.get("current")
        if not song:
            await interaction.response.send_message("ما فيه أغنية شغالة حالياً.", ephemeral=True)
            return
        added = add_favorite(interaction.user.id, song)
        if added:
            await interaction.response.send_message(f"❤️ تمت إضافة **{song['title']}** لمفضلتك.", ephemeral=True)
        else:
            await interaction.response.send_message("الأغنية موجودة أصلاً بمفضلتك.", ephemeral=True)

    @discord.ui.button(label="⏭️ سكيب", style=discord.ButtonStyle.secondary, row=1)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_room(interaction):
            return
        state = get_state(self.guild_id)
        vc = state["voice_client"]
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭️ تم تخطي الأغنية.", ephemeral=True)
        else:
            await interaction.response.send_message("ما فيه أغنية تشتغل.", ephemeral=True)

    @discord.ui.button(label="⏹️ إيقاف نهائي", style=discord.ButtonStyle.danger, row=1)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_room(interaction):
            return
        state = get_state(self.guild_id)
        vc = state["voice_client"]
        state["queue"].clear()
        state["current"] = None
        state["repeat"] = False
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
        await interaction.response.send_message("⏹️ تم إيقاف التشغيل ومسح القائمة.", ephemeral=True)


def _after_track(guild: discord.Guild, error):
    state = get_state(guild.id)
    if error:
        print(f"خطأ أثناء التشغيل: {error}")
    if state.get("manual_seek"):
        state["manual_seek"] = False
        return
    fut = asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)
    try:
        fut.result()
    except Exception as e:
        print(f"خطأ بعد التشغيل: {e}")


async def play_next(guild: discord.Guild):
    state = get_state(guild.id)
    vc = state["voice_client"]

    if vc is None or not vc.is_connected():
        return

    if state["repeat"] and state["current"] is not None:
        song = state["current"]
    elif state["queue"]:
        song = state["queue"].pop(0)
        state["current"] = song
    else:
        state["current"] = None
        return

    state["seek_offset"] = 0
    state["start_time"] = time.time()
    state["paused_at"] = None

    source = make_source(song["url"], volume=state.get("volume", 1.0))
    vc.play(source, after=lambda error: _after_track(guild, error))

    channel = state["text_channel"]
    if channel:
        loop = asyncio.get_event_loop()
        thumb_bytes = None
        if song.get("thumbnail"):
            try:
                thumb_bytes = await fetch_thumbnail_bytes(song["thumbnail"])
            except Exception as e:
                print(f"تعذر تحميل صورة الأغنية: {e}")

        if thumb_bytes:
            card_base = await loop.run_in_executor(
                None, build_base_card, thumb_bytes, song["title"], song.get("uploader", "")
            )
        else:
            card_base = await loop.run_in_executor(
                None, build_placeholder_card, song["title"], song.get("uploader", "")
            )
        state["card_base"] = card_base

        frame = render_card_frame(card_base, 0, song.get("duration") or 0, paused=False)
        file = discord.File(frame, filename="now_playing.png")
        embed = discord.Embed(title="🎶 يشتغل الآن", color=discord.Color.blurple())
        embed.set_image(url="attachment://now_playing.png")
        view = MusicControls(guild.id)
        msg = await channel.send(embed=embed, view=view, file=file)
        state["now_message"] = msg


@tasks.loop(seconds=15)
async def ensure_voice_connection():
    if VOICE_CHANNEL_ID == 0:
        return
    channel = bot.get_channel(VOICE_CHANNEL_ID)
    if channel is None:
        return
    guild = channel.guild
    state = get_state(guild.id)
    vc = guild.voice_client

    if vc is None or not vc.is_connected():
        try:
            new_vc = await channel.connect(reconnect=True, timeout=30)
            state["voice_client"] = new_vc
        except Exception as e:
            print(f"تعذر الاتصال بالروم الصوتي: {e}")
    else:
        state["voice_client"] = vc


@bot.event
async def on_voice_state_update(member, before, after):
    if member.id != bot.user.id:
        return
    if VOICE_CHANNEL_ID == 0:
        return
    guild = member.guild
    channel = bot.get_channel(VOICE_CHANNEL_ID)
    if channel is None:
        return
    if after.channel is None or after.channel.id != VOICE_CHANNEL_ID:
        await asyncio.sleep(2)
        try:
            if guild.voice_client:
                await guild.voice_client.move_to(channel)
            else:
                new_vc = await channel.connect(reconnect=True, timeout=30)
                get_state(guild.id)["voice_client"] = new_vc
        except Exception as e:
            print(f"تعذر إرجاع البوت للروم الصوتي: {e}")


@bot.event
async def on_ready():
    print(f"تم تسجيل الدخول باسم {bot.user}")
    if not ensure_voice_connection.is_running():
        ensure_voice_connection.start()
    if not update_now_playing_cards.is_running():
        update_now_playing_cards.start()


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    content = message.content.strip()
    guild = message.guild
    state = get_state(guild.id)

    if content == "مفضلتي":
        lst = favorites_data.get(str(message.author.id), [])
        if not lst:
            await message.channel.send("ما عندك أغاني بالمفضلة عند هذا البوت بعد.")
            return
        lines = [f"{i+1}. [{s['title']}]({s['webpage_url']})" for i, s in enumerate(lst[:15])]
        embed = discord.Embed(title="❤️ مفضلتك", description="\n".join(lines), color=discord.Color.red())
        await message.channel.send(embed=embed)
        return

    is_target_command = (
        content.startswith("ش")
        or content == "س"
        or content == "تكرار"
        or content.startswith("قدم")
    )

    if is_target_command and not in_target_channel(message.author):
        return

    state["text_channel"] = message.channel

    if state["voice_client"] is None or not state["voice_client"].is_connected():
        if guild.voice_client:
            state["voice_client"] = guild.voice_client

    if content.startswith("ش"):
        query = content[1:].strip()
        if not query:
            await message.channel.send("اكتب اسم الأغنية بعد حرف ش، مثال: ش فيروز")
            return

        await message.channel.send(f"🔎 يبحث عن: **{query}** ...")
        try:
            song = await extract_song(query)
        except Exception as e:
            await message.channel.send(f"⚠️ ما قدرت ألقى الأغنية: {e}")
            return

        vc = state["voice_client"]
        if vc is None or not vc.is_connected():
            await message.channel.send("⚠️ البوت مو متصل بروم صوتي حالياً.")
            return

        if vc.is_playing() or vc.is_paused():
            state["queue"].append(song)
            await message.channel.send(f"➕ تمت الإضافة للقائمة: **{song['title']}**")
        else:
            state["queue"].append(song)
            await play_next(guild)
        return

    if content == "س":
        vc = state["voice_client"]
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await message.channel.send("⏭️ تم السكيب.")
        else:
            await message.channel.send("ما فيه أغنية تشتغل حالياً.")
        return

    if content == "تكرار":
        state["repeat"] = not state["repeat"]
        status = "✅ تم تفعيل التكرار" if state["repeat"] else "❌ تم إيقاف التكرار"
        await message.channel.send(status)
        return

    if content.startswith("قدم"):
        parts = content.split()
        seconds = DEFAULT_SEEK_SECONDS
        if len(parts) > 1 and parts[1].isdigit():
            seconds = int(parts[1])

        vc = state["voice_client"]
        if vc is None or state["current"] is None or not (vc.is_playing() or vc.is_paused()):
            await message.channel.send("ما فيه أغنية تشتغل حالياً.")
            return

        elapsed = time.time() - state["start_time"] + state["seek_offset"]
        new_position = int(elapsed) + seconds
        song = state["current"]

        if song.get("duration") and new_position >= song["duration"]:
            state["manual_seek"] = True
            vc.stop()
            await asyncio.sleep(0.3)
            await play_next(guild)
            await message.channel.send("⏭️ وصلنا لنهاية الأغنية، ننتقل للي بعدها.")
            return

        state["manual_seek"] = True
        vc.stop()

        source = make_source(song["url"], seek_seconds=new_position, volume=state.get("volume", 1.0))
        state["seek_offset"] = new_position
        state["start_time"] = time.time()
        state["paused_at"] = None
        vc.play(source, after=lambda error: _after_track(guild, error))
        await message.channel.send(f"⏩ تم التقديم {seconds} ثانية.")
        return

    await bot.process_commands(message)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ لازم تحط التوكن في ملف .env داخل المتغير DISCORD_TOKEN")
    bot.run(TOKEN)
