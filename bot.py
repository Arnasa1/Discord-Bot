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

# yt-dlp options
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
    'source_address': '0.0.0.0'
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

# If FFmpeg is not in PATH, specify the executable path
# Uncomment and modify the line below if needed:
# discord.opus.load_opus('path/to/opus.dll')  # For Opus codec
# Or set FFmpeg path directly:
FFMPEG_PATH = 'C:\\ffmpeg\\bin\\ffmpeg.exe'  # Update this path

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        # Use custom FFmpeg path if specified
        if 'FFMPEG_PATH' in globals():
            return cls(discord.FFmpegPCMAudio(filename, executable=FFMPEG_PATH, **ffmpeg_options), data=data)
        else:
            return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')

@bot.command(name='join', help='Joins a voice channel')
async def join(ctx):
    if not ctx.message.author.voice:
        await ctx.send("You are not connected to a voice channel!")
        return
    
    channel = ctx.message.author.voice.channel
    voice_client = await channel.connect()
    voice_clients[ctx.guild.id] = voice_client

@bot.command(name='leave', help='Leaves the voice channel')
async def leave(ctx):
    voice_client = voice_clients.get(ctx.guild.id)
    if voice_client:
        await voice_client.disconnect()
        del voice_clients[ctx.guild.id]
        if ctx.guild.id in music_queues:
            music_queues[ctx.guild.id].clear()

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
            await ctx.send(f'**{player.title}** has been added to the queue')

    except Exception as e:
        await ctx.send(f"An error occurred: {str(e)}")

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
    await ctx.send(f'**Now playing:** {player.title}')

@bot.command(name='pause', help='Pauses the current song')
async def pause(ctx):
    voice_client = voice_clients.get(ctx.guild.id)
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await ctx.send("⏸️ Paused")

@bot.command(name='resume', help='Resumes the current song')
async def resume(ctx):
    voice_client = voice_clients.get(ctx.guild.id)
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await ctx.send("▶️ Resumed")

@bot.command(name='skip', help='Skips the current song')
async def skip(ctx):
    voice_client = voice_clients.get(ctx.guild.id)
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await ctx.send("⏭️ Skipped")

@bot.command(name='queue', help='Shows the current queue')
async def show_queue(ctx):
    if ctx.guild.id not in music_queues or len(music_queues[ctx.guild.id]) == 0:
        await ctx.send("The queue is empty!")
        return

    queue_list = []
    for i, player in enumerate(music_queues[ctx.guild.id], 1):
        queue_list.append(f"{i}. {player.title}")

    queue_text = "\n".join(queue_list[:10])  # Show first 10 songs
    if len(music_queues[ctx.guild.id]) > 10:
        queue_text += f"\n... and {len(music_queues[ctx.guild.id]) - 10} more"

    embed = discord.Embed(title="Music Queue", description=queue_text, color=0x00ff00)
    await ctx.send(embed=embed)

@bot.command(name='stop', help='Stops the music and clears the queue')
async def stop(ctx):
    voice_client = voice_clients.get(ctx.guild.id)
    if voice_client:
        if ctx.guild.id in music_queues:
            music_queues[ctx.guild.id].clear()
        voice_client.stop()
        await ctx.send("⏹️ Stopped and cleared queue")

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
        await ctx.send("Volume must be between 0 and 100")

# Keep-alive function for Replit
from flask import Flask
from threading import Thread
import os

app = Flask('')

@app.route('/')
def home():
    return "Discord Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

# Run the bot
if __name__ == "__main__":
    keep_alive()
    # Get token from environment variable
    token = os.getenv('DISCORD_TOKEN')
    if token:
        bot.run(token)
    else:
        print("Error: DISCORD_TOKEN environment variable not set!")