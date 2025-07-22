import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import random
import json
import ssl
import urllib3
from collections import deque
from dotenv import load_dotenv
import certifi
import aiohttp
from urllib.parse import urlparse
import time

# SSL certificate handling
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
os.environ['CURL_CA_BUNDLE'] = certifi.where()

# Disable SSL warnings (for development only)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Music data for each guild
guild_data = {}

# Base yt-dlp options with FORCED noplaylist
BASE_YTDL_OPTIONS = {
    'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio[ext=mp3]/bestaudio/best[height<=480]',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,  # ALWAYS force single video
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'cookiefile': None,
    'extract_flat': False,
    'age_limit': None,
    'prefer_free_formats': True,
    # Force single video extraction
    'playlist_items': '1',  # Only extract first item if somehow a playlist is encountered
    'max_downloads': 1,     # Maximum 1 download
    # Better audio quality options
    'audioquality': '0',  # Best quality
    'audioformat': 'best',
    # SSL and certificate fixes
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    },
    'extractor_args': {
        'youtube': {
            'skip': ['hls', 'dash'],
            'player_skip': ['js'],
        }
    },
    # Additional SSL options
    'geo_bypass': True,
    'geo_bypass_country': 'US',
}

def get_safe_ytdl_options(**additional_options):
    """Create yt-dlp options with GUARANTEED noplaylist enforcement"""
    options = BASE_YTDL_OPTIONS.copy()
    options.update(additional_options)
    
    # FORCE these options to prevent playlist extraction
    options['noplaylist'] = True
    options['playlist_items'] = '1'
    options['max_downloads'] = 1
    options['extract_flat'] = False
    
    return options

# Improved yt-dlp options with better format selection
ytdl_format_options = get_safe_ytdl_options()

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 32M -analyzeduration 10M',
    'options': '-vn -filter:a "volume=0.5"'
}

# Try to find FFmpeg automatically
FFMPEG_PATH = None
for path in ['ffmpeg', 'C:\\ffmpeg\\bin\\ffmpeg.exe', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']:
    try:
        os.system(f'"{path}" -version > /dev/null 2>&1' if os.name != 'nt' else f'"{path}" -version >nul 2>&1')
        FFMPEG_PATH = path
        break
    except:
        continue

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

def get_guild_data(guild_id):
    """Get or create guild data"""
    if guild_id not in guild_data:
        guild_data[guild_id] = {
            'queue': deque(),
            'voice_client': None,
            'loop_mode': 'off',  # off, track, queue
            'volume': 0.5,
            'now_playing': None,
            'history': deque(maxlen=50),
            'autoplay': False,
            'bass_boost': False
        }
    return guild_data[guild_id]

def ensure_single_video_url(url):
    """Ensure URL points to a single video, not a playlist"""
    if 'youtube.com' in url or 'youtu.be' in url:
        # Remove playlist parameters from YouTube URLs
        if 'list=' in url:
            # Extract video ID and create clean URL
            if 'watch?v=' in url:
                video_id = url.split('watch?v=')[1].split('&')[0]
                return f"https://www.youtube.com/watch?v={video_id}"
            elif 'youtu.be/' in url:
                video_id = url.split('youtu.be/')[1].split('?')[0]
                return f"https://www.youtube.com/watch?v={video_id}"
    return url

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('webpage_url') or data.get('url')
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail')
        self.uploader = data.get('uploader')
        
    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        
        # Ensure single video URL
        url = ensure_single_video_url(url)
        
        # Progressive format selection - start with most compatible, fallback to more specific
        format_configs = [
            # Most compatible formats first
            {'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio'},
            {'format': 'bestaudio/best[height<=480]'},
            {'format': 'best[height<=360]/worst'},
            # Fallback with any available format
            {'format': 'bestaudio'},
            {'format': 'best'},
            {'format': 'worst'},
        ]
        
        last_error = None
        
        for i, format_config in enumerate(format_configs):
            try:
                # Create a new ytdl instance with specific format and FORCED noplaylist
                temp_options = get_safe_ytdl_options(**format_config)
                temp_options.update({'nocheckcertificate': True})
                
                temp_ytdl = yt_dlp.YoutubeDL(temp_options)
                
                print(f"Format attempt {i+1}: {format_config['format']} (noplaylist=True)")
                data = await loop.run_in_executor(
                    None, 
                    lambda: temp_ytdl.extract_info(url, download=not stream)
                )
                
                # CRITICAL: Always handle potential playlist entries
                if 'entries' in data:
                    if len(data['entries']) > 0:
                        # Force only first entry
                        data = data['entries'][0]
                        print(f"⚠️  Playlist detected! Using only first entry: {data.get('title', 'Unknown')}")
                    else:
                        raise Exception("No results found")
                
                if not data:
                    raise Exception("No data extracted")
                
                # Check if we have a valid URL
                if not data.get('url'):
                    raise Exception("No audio URL found")
                        
                filename = data['url'] if stream else temp_ytdl.prepare_filename(data)
                
                # Create FFmpeg source with better options
                if FFMPEG_PATH:
                    source = discord.FFmpegPCMAudio(filename, executable=FFMPEG_PATH, **ffmpeg_options)
                else:
                    source = discord.FFmpegPCMAudio(filename, **ffmpeg_options)
                    
                print(f"✅ Successfully extracted: {data.get('title', 'Unknown')} with format: {format_config['format']}")
                return cls(source, data=data)
                
            except Exception as e:
                last_error = e
                error_msg = str(e)
                print(f"❌ Format attempt {i+1} failed: {error_msg}")
                
                # Don't retry if it's a "Video unavailable" error
                if "Video unavailable" in error_msg or "Private video" in error_msg:
                    break
                    
                if i < len(format_configs) - 1:
                    print(f"🔄 Trying next format...")
                    await asyncio.sleep(0.5)  # Brief delay between attempts
                continue
        
        # Final fallback attempt with minimal options
        try:
            return await cls._minimal_extraction(url, loop, stream)
        except:
            pass
        
        raise Exception(f"All format attempts failed. Last error: {str(last_error)}")
    
    @classmethod
    async def _minimal_extraction(cls, url, loop, stream):
        """Minimal extraction method as last resort with FORCED noplaylist"""
        try:
            minimal_options = get_safe_ytdl_options(
                quiet=False,
                no_warnings=False,
                nocheckcertificate=True,
                ignoreerrors=True,
                format='worst/best',  # Accept any format
                default_search='ytsearch' if not url.startswith('http') else None,
            )
            
            minimal_ytdl = yt_dlp.YoutubeDL(minimal_options)
            
            print("🔄 Trying minimal extraction method (noplaylist=True)...")
            data = await loop.run_in_executor(
                None, 
                lambda: minimal_ytdl.extract_info(url, download=False)
            )
            
            # CRITICAL: Handle playlist entries in minimal extraction too
            if 'entries' in data and data['entries']:
                data = data['entries'][0]
                print(f"⚠️  Minimal extraction: Playlist detected! Using only first entry: {data.get('title', 'Unknown')}")
            
            if not data or not data.get('url'):
                raise Exception("Minimal extraction failed - no URL found")
            
            # Create a basic audio source
            source = discord.FFmpegPCMAudio(data['url'], **ffmpeg_options)
            print(f"✅ Minimal extraction successful: {data.get('title', 'Unknown')}")
            return cls(source, data=data)
            
        except Exception as e:
            raise Exception(f"Minimal extraction failed: {str(e)}")

    @classmethod
    async def search_youtube(cls, query, limit=5):
        """Search YouTube and return results with format checking (SINGLE VIDEOS ONLY)"""
        search_configs = [
            # Standard search with forced single video
            get_safe_ytdl_options(default_search='ytsearch', format='bestaudio'),
            # Search with no SSL verification
            get_safe_ytdl_options(default_search='ytsearch', nocheckcertificate=True, format='bestaudio'),
            # Minimal search config
            get_safe_ytdl_options(
                default_search='ytsearch',
                nocheckcertificate=True,
                quiet=False,
                extract_flat=True,
                format='best'
            )
        ]
        
        for i, config in enumerate(search_configs):
            try:
                temp_ytdl = yt_dlp.YoutubeDL(config)
                
                loop = asyncio.get_event_loop()
                search_data = await loop.run_in_executor(
                    None, 
                    lambda: temp_ytdl.extract_info(f"ytsearch{limit}:{query}", download=False)
                )
                
                if 'entries' in search_data and search_data['entries']:
                    # Filter out any playlist entries (extra safety)
                    single_videos = []
                    for entry in search_data['entries']:
                        if entry and not entry.get('_type') == 'playlist':
                            single_videos.append(entry)
                    return single_videos[:limit]  # Ensure we don't exceed limit
                    
            except Exception as e:
                print(f"Search attempt {i+1} failed: {e}")
                if i < len(search_configs) - 1:
                    continue
        
        return []

@bot.event
async def on_ready():
    print(f'🎵 {bot.user} is now jamming on Discord!')
    print(f'🚫 Playlist protection: ENABLED (noplaylist=True always)')
    await bot.change_presence(activity=discord.Game(name="🎵 !help for commands"))

@bot.event
async def on_voice_state_update(member, before, after):
    """Handle bot being alone in voice channel"""
    if member == bot.user:
        return
        
    # Check if bot is alone in voice channel
    for guild_id, data in guild_data.items():
        if data['voice_client'] and data['voice_client'].channel:
            members = [m for m in data['voice_client'].channel.members if not m.bot]
            if len(members) == 0:
                # Bot is alone, disconnect after 5 minutes
                await asyncio.sleep(300)
                if data['voice_client'] and data['voice_client'].is_connected():
                    members = [m for m in data['voice_client'].channel.members if not m.bot]
                    if len(members) == 0:
                        await data['voice_client'].disconnect()
                        data['voice_client'] = None

@bot.command(name='join', help='🎤 Join your voice channel')
async def join(ctx):
    if not ctx.author.voice:
        embed = discord.Embed(
            title="❌ Error", 
            description="You need to be in a voice channel first!", 
            color=0xff0000
        )
        await ctx.send(embed=embed)
        return
    
    channel = ctx.author.voice.channel
    data = get_guild_data(ctx.guild.id)
    
    if data['voice_client'] and data['voice_client'].is_connected():
        await data['voice_client'].move_to(channel)
    else:
        data['voice_client'] = await channel.connect()
    
    embed = discord.Embed(
        title="🎤 Joined Voice Channel", 
        description=f"Connected to **{channel.name}**", 
        color=0x00ff00
    )
    await ctx.send(embed=embed)

@bot.command(name='leave', aliases=['disconnect'], help='👋 Leave the voice channel')
async def leave(ctx):
    data = get_guild_data(ctx.guild.id)
    
    if data['voice_client']:
        await data['voice_client'].disconnect()
        data['voice_client'] = None
        data['queue'].clear()
        data['now_playing'] = None
        
        embed = discord.Embed(
            title="👋 Disconnected", 
            description="See you later! Queue cleared.", 
            color=0x00ff00
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ Not connected to a voice channel!")

@bot.command(name='play', aliases=['p'], help='🎵 Play a song (YouTube URL or search) - SINGLE VIDEOS ONLY')
async def play(ctx, *, query):
    data = get_guild_data(ctx.guild.id)
    
    # Join voice channel if not connected
    if not data['voice_client']:
        if not ctx.author.voice:
            await ctx.send("❌ You need to be in a voice channel first!")
            return
        channel = ctx.author.voice.channel
        data['voice_client'] = await channel.connect()
    
    # Send initial message
    loading_msg = await ctx.send("🔍 Searching and processing audio (single video only)...")
    
    try:
        # Clean URL if it's a YouTube playlist link
        if query.startswith('http'):
            query = ensure_single_video_url(query)
            if 'list=' in query:
                await loading_msg.edit(content="⚠️ Playlist URL detected! Extracting single video only...")
        
        # Check if it's a URL or search query
        if not (query.startswith('http://') or query.startswith('https://')):
            query = f"ytsearch1:{query}"  # Force single result search
        
        player = await YTDLSource.from_url(query, loop=bot.loop, stream=True)
        player.volume = data['volume']
        
        # Add to queue
        data['queue'].append(player)
        
        if not data['voice_client'].is_playing() and not data['voice_client'].is_paused():
            await loading_msg.delete()
            await play_next(ctx)
        else:
            embed = discord.Embed(
                title="📝 Added to Queue",
                description=f"**{player.title}**\n`Position: {len(data['queue'])} (Single video)`",
                color=0x00ff00
            )
            if player.thumbnail:
                embed.set_thumbnail(url=player.thumbnail)
            embed.set_footer(text="🚫 Playlists are automatically filtered to single videos")
            await loading_msg.edit(content="", embed=embed)
            
    except Exception as e:
        error_msg = str(e)
        embed = discord.Embed(
            title="❌ Playback Error",
            color=0xff0000
        )
        
        if "certificate verify failed" in error_msg or "SSL" in error_msg:
            embed.description = "**SSL Certificate Error**\n\nTry these solutions:\n1. Update yt-dlp: `pip install -U yt-dlp`\n2. Try a different video/search term\n3. Check your internet connection"
            embed.add_field(
                name="Alternative", 
                value="Try using `!ytfix` command to attempt a fix", 
                inline=False
            )
        elif "No results found" in error_msg:
            embed.description = f"**No results found for:** `{query}`\n\nTry:\n• Different search terms\n• Direct YouTube URL\n• Check spelling"
        elif "Video unavailable" in error_msg or "Private video" in error_msg:
            embed.description = f"**Video is unavailable**\n\nThis video might be:\n• Private or deleted\n• Region-blocked\n• Age-restricted\n\nTry a different video or search term."
        elif "Requested format is not available" in error_msg:
            embed.description = f"**Audio format not available**\n\nThis video doesn't have compatible audio formats.\nTry:\n• A different video\n• Using `!test` to diagnose issues\n• Updating yt-dlp with `!ytfix`"
        else:
            embed.description = f"**Error:** {error_msg}\n\nTry a different video or search term."
        
        embed.set_footer(text="🚫 Remember: Only single videos are supported, playlists are filtered out")
        await loading_msg.edit(content="", embed=embed)

@bot.command(name='search', help='🔍 Search YouTube for songs (SINGLE VIDEOS ONLY)')
async def search(ctx, *, query):
    async with ctx.typing():
        results = await YTDLSource.search_youtube(query, limit=5)
        
        if not results:
            await ctx.send("❌ No results found!")
            return
        
        embed = discord.Embed(title=f"🔍 Search Results for: {query}", color=0x0099ff)
        
        for i, result in enumerate(results, 1):
            duration = f"{result.get('duration', 0) // 60}:{result.get('duration', 0) % 60:02d}" if result.get('duration') else "Unknown"
            embed.add_field(
                name=f"{i}. {result.get('title', 'Unknown')}",
                value=f"Duration: {duration}\nUploader: {result.get('uploader', 'Unknown')}",
                inline=False
            )
        
        embed.set_footer(text="🚫 Only single videos shown | Use !play <number> or !play <search term> to play a song")
        await ctx.send(embed=embed)

async def play_next(ctx):
    data = get_guild_data(ctx.guild.id)
    
    if not data['voice_client'] or not data['voice_client'].is_connected():
        return
    
    # Handle loop modes
    if data['loop_mode'] == 'track' and data['now_playing']:
        # Replay current track
        player = data['now_playing']
    elif data['loop_mode'] == 'queue' and data['now_playing']:
        # Add current track back to end of queue
        data['queue'].append(data['now_playing'])
        if len(data['queue']) == 0:
            return
        player = data['queue'].popleft()
    else:
        # Normal mode - get next from queue
        if len(data['queue']) == 0:
            data['now_playing'] = None
            return
        player = data['queue'].popleft()
    
    # Add to history
    if data['now_playing']:
        data['history'].append(data['now_playing'])
    
    data['now_playing'] = player
    
    def after_playing(error):
        if error:
            print(f'Player error: {error}')
        
        # Play next song
        coro = play_next(ctx)
        fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
        try:
            fut.result()
        except Exception as e:
            print(f"Error in play_next: {e}")
    
    try:
        # Recreate the audio source for replay
        if data['loop_mode'] == 'track':
            new_player = await YTDLSource.from_url(player.url, loop=bot.loop, stream=True)
            new_player.volume = data['volume']
            data['voice_client'].play(new_player, after=after_playing)
        else:
            player.volume = data['volume']
            data['voice_client'].play(player, after=after_playing)
        
        # Send now playing embed
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**{player.title}**",
            color=0x00ff00
        )
        
        if player.duration:
            duration = f"{player.duration // 60}:{player.duration % 60:02d}"
            embed.add_field(name="Duration", value=duration, inline=True)
        
        if player.uploader:
            embed.add_field(name="Channel", value=player.uploader, inline=True)
        
        embed.add_field(name="Volume", value=f"{int(data['volume'] * 100)}%", inline=True)
        
        if data['loop_mode'] != 'off':
            embed.add_field(name="Loop", value=data['loop_mode'].title(), inline=True)
        
        if player.thumbnail:
            embed.set_thumbnail(url=player.thumbnail)
        
        embed.set_footer(text=f"Queue: {len(data['queue'])} songs | 🚫 Single videos only")
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ Error playing track: {str(e)}")
        # Try to play next song on error
        await play_next(ctx)

# Enhanced diagnostic command
@bot.command(name='formats', help='📋 Check available formats for a video')
async def check_formats(ctx, *, url):
    """Check available formats for a specific video"""
    try:
        # Ensure single video
        url = ensure_single_video_url(url)
        
        # Create a ytdl instance just for listing formats with noplaylist
        list_options = get_safe_ytdl_options(
            listformats=True,
            quiet=False,
            no_warnings=False
        )
        list_ytdl = yt_dlp.YoutubeDL(list_options)
        
        loop = asyncio.get_event_loop()
        
        # This will print formats to console
        await ctx.send("🔍 Checking available formats for single video... (check console output)")
        
        await loop.run_in_executor(
            None, 
            lambda: list_ytdl.extract_info(url, download=False)
        )
        
        await ctx.send("✅ Format check complete! Check the console for available formats.")
        
    except Exception as e:
        await ctx.send(f"❌ Error checking formats: {str(e)}")

@bot.command(name='ytfix', help='🔧 Attempt to fix YouTube extraction issues')
async def youtube_fix(ctx):
    embed = discord.Embed(
        title="🔧 YouTube Extraction Troubleshooting",
        color=0x0099ff
    )
    
    embed.add_field(
        name="1. Update yt-dlp",
        value="```\npip install -U yt-dlp\n```",
        inline=False
    )
    
    embed.add_field(
        name="2. Check for format issues",
        value="Use `!formats <youtube_url>` to see available formats for a specific video",
        inline=False
    )
    
    embed.add_field(
        name="3. Try different videos",
        value="Some videos may have restricted audio formats. Try popular music videos which usually have better format availability.",
        inline=False
    )
    
    embed.add_field(
        name="4. Update certificates",
        value="```\npip install -U certifi urllib3\n```",
        inline=False
    )
    
    embed.add_field(
        name="5. Playlist Protection",
        value="This bot is configured to ALWAYS extract single videos only, even from playlist URLs.",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command(name='status', help='📊 Show bot status and configuration')
async def status(ctx):
    embed = discord.Embed(
        title="🤖 Bot Status & Configuration",
        color=0x0099ff
    )
    
    embed.add_field(
        name="🚫 Playlist Protection",
        value="✅ ENABLED - Always extracts single videos only",
        inline=False
    )
    
    embed.add_field(
        name="⚙️ yt-dlp Options",
        value=f"• noplaylist: `True`\n• playlist_items: `1`\n• max_downloads: `1`\n• extract_flat: `False`",
        inline=False
    )
    
    data = get_guild_data(ctx.guild.id)
    voice_status = "Connected" if data['voice_client'] and data['voice_client'].is_connected() else "Not connected"
    
    embed.add_field(
        name="🎵 Voice Status",
        value=voice_status,
        inline=True
    )
    
    embed.add_field(
        name="📝 Queue Length",
        value=str(len(data['queue'])),
        inline=True
    )
    
    embed.add_field(
        name="🔁 Loop Mode",
        value=data['loop_mode'].title(),
        inline=True
    )
    
    await ctx.send(embed=embed)

# Error handling
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing required argument! Use `!help {ctx.command}` for usage.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Invalid argument! Use `!help {ctx.command}` for usage.")
    else:
        await ctx.send(f"❌ An error occurred: {str(error)}")
        print(f"Command error: {error}")

# Run the bot
if __name__ == "__main__":
    # Get token from environment variable
    token = os.getenv('DISCORD_TOKEN')
    if token:
        try:
            bot.run(token)
        except Exception as e:
            print(f"Error running bot: {e}")
            print("Make sure your DISCORD_TOKEN is valid and the bot has proper permissions!")
    else:
        print("Error: DISCORD_TOKEN environment variable not set!")
        print("Create a .env file with: DISCORD_TOKEN=your_bot_token_here")