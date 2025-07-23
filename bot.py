import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
from collections import deque
from dotenv import load_dotenv

load_dotenv()

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Music queue for each guild
music_queues = {}
voice_clients = {}

# Check if cookies file exists
cookies_path = 'cookies.txt'
cookies_exists = os.path.exists(cookies_path)

# yt-dlp options with conditional cookies
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': False,
}

# Add cookies if file exists
if cookies_exists:
    ytdl_format_options['cookiefile'] = cookies_path
    print("✅ Cookies file found and loaded")
else:
    print("⚠️ No cookies file found - some videos may be restricted")

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration')
        self.uploader = data.get('uploader')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        except yt_dlp.DownloadError as e:
            if "Sign in to confirm your age" in str(e) or "This video may be inappropriate" in str(e):
                raise Exception("This video is age-restricted and requires authentication. Please ensure your cookies.txt file is valid and up-to-date.")
            elif "Private video" in str(e):
                raise Exception("This video is private and cannot be accessed.")
            elif "Video unavailable" in str(e):
                raise Exception("This video is unavailable in your region or has been removed.")
            else:
                raise Exception(f"Could not extract video information: {str(e)}")

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is ready to serve {len(bot.guilds)} guilds')
    if cookies_exists:
        print("✅ Running with cookies support")
    else:
        print("⚠️ Running without cookies - some content may be restricted")

@bot.command(name='join', help='Joins a voice channel')
async def join(ctx):
    if not ctx.message.author.voice:
        await ctx.send("You are not connected to a voice channel!")
        return
    
    channel = ctx.message.author.voice.channel
    voice_client = await channel.connect()
    voice_clients[ctx.guild.id] = voice_client
    await ctx.send(f"✅ Joined **{channel.name}**")

@bot.command(name='leave', help='Leaves the voice channel')
async def leave(ctx):
    voice_client = voice_clients.get(ctx.guild.id)
    if voice_client:
        await voice_client.disconnect()
        del voice_clients[ctx.guild.id]
        if ctx.guild.id in music_queues:
            music_queues[ctx.guild.id].clear()
        await ctx.send("👋 Left the voice channel")
    else:
        await ctx.send("I'm not connected to a voice channel!")

@bot.command(name='play', help='Plays a song from YouTube')
async def play(ctx, *, url):
    try:
        # Initialize queue if it doesn't exist
        if ctx.guild.id not in music_queues:
            music_queues[ctx.guild.id] = deque()

        # Join voice channel if not already connected
        if ctx.guild.id not in voice_clients:
            if not ctx.message.author.voice:
                await ctx.send("You need to connect to a voice channel first!")
                return
            channel = ctx.message.author.voice.channel
            voice_client = await channel.connect()
            voice_clients[ctx.guild.id] = voice_client

        voice_client = voice_clients[ctx.guild.id]

        # Add to queue
        async with ctx.typing():
            player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
            music_queues[ctx.guild.id].append(player)

        if not voice_client.is_playing():
            await play_next(ctx)
        else:
            duration_str = f" ({format_duration(player.duration)})" if player.duration else ""
            uploader_str = f" by **{player.uploader}**" if player.uploader else ""
            await ctx.send(f'📝 **{player.title}**{duration_str}{uploader_str} has been added to the queue')

    except Exception as e:
        error_msg = str(e)
        if "age-restricted" in error_msg.lower():
            await ctx.send("❌ This video is age-restricted. Make sure you have a valid cookies.txt file to access age-restricted content.")
        elif "private video" in error_msg.lower():
            await ctx.send("❌ This video is private and cannot be accessed.")
        elif "unavailable" in error_msg.lower():
            await ctx.send("❌ This video is unavailable or has been removed.")
        else:
            await ctx.send(f"❌ An error occurred: {error_msg}")

def format_duration(seconds):
    if not seconds:
        return "Unknown"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes}:{seconds:02d}"

async def play_next(ctx):
    voice_client = voice_clients.get(ctx.guild.id)
    if not voice_client or ctx.guild.id not in music_queues:
        return

    queue = music_queues[ctx.guild.id]
    if len(queue) == 0:
        return

    player = queue.popleft()
    
    def after_playing(error):
        if error:
            print(f'Player error: {error}')
        
        # Play next song after current one finishes
        coro = play_next(ctx)
        fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
        try:
            fut.result()
        except:
            pass

    voice_client.play(player, after=after_playing)
    duration_str = f" ({format_duration(player.duration)})" if player.duration else ""
    uploader_str = f" by **{player.uploader}**" if player.uploader else ""
    await ctx.send(f'🎵 **Now playing:** {player.title}{duration_str}{uploader_str}')

@bot.command(name='pause', help='Pauses the current song')
async def pause(ctx):
    voice_client = voice_clients.get(ctx.guild.id)
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await ctx.send("⏸️ Paused")
    else:
        await ctx.send("Nothing is currently playing!")

@bot.command(name='resume', help='Resumes the current song')
async def resume(ctx):
    voice_client = voice_clients.get(ctx.guild.id)
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await ctx.send("▶️ Resumed")
    else:
        await ctx.send("Nothing is currently paused!")

@bot.command(name='skip', help='Skips the current song')
async def skip(ctx):
    voice_client = voice_clients.get(ctx.guild.id)
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await ctx.send("⏭️ Skipped")
    else:
        await ctx.send("Nothing is currently playing!")

@bot.command(name='queue', help='Shows the current queue')
async def show_queue(ctx):
    if ctx.guild.id not in music_queues or len(music_queues[ctx.guild.id]) == 0:
        await ctx.send("The queue is empty!")
        return

    queue_list = []
    for i, player in enumerate(music_queues[ctx.guild.id], 1):
        duration_str = f" ({format_duration(player.duration)})" if player.duration else ""
        queue_list.append(f"{i}. {player.title}{duration_str}")

    queue_text = "\n".join(queue_list[:10])  # Show first 10 songs
    if len(music_queues[ctx.guild.id]) > 10:
        queue_text += f"\n... and {len(music_queues[ctx.guild.id]) - 10} more songs"

    embed = discord.Embed(title="🎵 Music Queue", description=queue_text, color=0x00ff00)
    await ctx.send(embed=embed)

@bot.command(name='stop', help='Stops the music and clears the queue')
async def stop(ctx):
    voice_client = voice_clients.get(ctx.guild.id)
    if voice_client:
        if ctx.guild.id in music_queues:
            music_queues[ctx.guild.id].clear()
        voice_client.stop()
        await ctx.send("⏹️ Stopped and cleared queue")
    else:
        await ctx.send("Nothing is currently playing!")

@bot.command(name='volume', help='Changes the volume (0-100)')
async def volume(ctx, volume: int):
    voice_client = voice_clients.get(ctx.guild.id)
    if not voice_client:
        await ctx.send("Not connected to a voice channel!")
        return

    if 0 <= volume <= 100:
        if voice_client.source:
            voice_client.source.volume = volume / 100
            await ctx.send(f"🔊 Volume set to {volume}%")
        else:
            await ctx.send("No audio source is currently playing!")
    else:
        await ctx.send("Volume must be between 0 and 100")

@bot.command(name='nowplaying', help='Shows the currently playing song', aliases=['np'])
async def now_playing(ctx):
    voice_client = voice_clients.get(ctx.guild.id)
    if not voice_client or not voice_client.is_playing():
        await ctx.send("Nothing is currently playing!")
        return
    
    if hasattr(voice_client.source, 'title'):
        source = voice_client.source
        duration_str = f" ({format_duration(source.duration)})" if hasattr(source, 'duration') and source.duration else ""
        uploader_str = f" by **{source.uploader}**" if hasattr(source, 'uploader') and source.uploader else ""
        await ctx.send(f'🎵 **Now playing:** {source.title}{duration_str}{uploader_str}')
    else:
        await ctx.send("Currently playing audio")

@bot.command(name='clear', help='Clears the queue without stopping current song')
async def clear_queue(ctx):
    if ctx.guild.id in music_queues:
        queue_size = len(music_queues[ctx.guild.id])
        music_queues[ctx.guild.id].clear()
        await ctx.send(f"🗑️ Cleared {queue_size} songs from the queue")
    else:
        await ctx.send("The queue is already empty!")

# Error handling
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing required argument. Use `!help {ctx.command}` for usage information.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Invalid argument. Use `!help {ctx.command}` for usage information.")
    else:
        await ctx.send(f"❌ An error occurred: {str(error)}")
        print(f"Unhandled error: {error}")

token = os.getenv('DISCORD_TOKEN')
if not token:
    print("❌ Error: DISCORD_TOKEN environment variable not found!")
    print("Please set your Discord bot token in the environment variables.")
else:
    bot.run(token)
