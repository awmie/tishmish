import nextcord
from nextcord import interactions
from nextcord.ext import commands, tasks
import nextwave
from nextwave.ext import spotify
import numpy as np
import asyncio
import datetime
import os
from dotenv import load_dotenv

# I N T E N T S
intents = nextcord.Intents(messages=True, guilds=True)
intents.guild_messages = True
intents.members = True
intents.message_content = True
intents.voice_states = True
intents.emojis_and_stickers = True
all_intents = intents.all()
all_intents = True

bot = commands.Bot(
    intents=intents,
    description="Premium quality music bot for free!\nUse headphones for better quality <3",
)
# some useful variables

global user_arr, user_dict
user_dict = {}
user_arr = np.array([])
setattr(nextwave.Player, "lq", False)
setattr(nextwave.Player, "autoplay", False)
embed_color = nextcord.Color.from_rgb(128, 67, 255)

# # T I S M I S H help-command
@commands.cooldown(1, 1, commands.BucketType.user)
@bot.slash_command(name="help", description="All you need")
async def help(interaction: nextcord.Interaction, helpstr: str = nextcord.SlashOption(
    name='help_choices', description='Choose one of the help commands',
    required=False, choices={"member commands", "tm commands"}
)):
    # List of TM and Member commands
    commands_dict = {
        "tm commands": [
<<<<<<< HEAD
            skip_command, del_command, move_command, clear_command, seek_command, 
            volume_command, skipto_command, shuffle_command, loop_command, 
            disconnect_command, loopqueue_command, set_role_command, 
            spotifyplay_command, restart_command, predict_command
        ],
        "member commands": [
            ping_command, play_command, pause_command, resume_command, 
=======
            skip_command, del_command, move_command, clear_command, seek_command,
            volume_command, skipto_command, shuffle_command, loop_command,
            disconnect_command, loopqueue_command, restart_command, predict_command
        ],
        "member commands": [
            ping_command, play_command, pause_command, resume_command,
>>>>>>> 8b882de (auto queue)
            nowplaying_command, queue_command, save_command
        ]
    }

    # Function to format the command list
    def format_command_list(cmd_list):
        return "\n\n".join([f"**/{cmd.name}** - `function: {cmd.description}`" for cmd in cmd_list])

    # Check the chosen help category and prepare the corresponding embed
    if helpstr in commands_dict:
        embed = nextcord.Embed(
            title=f"{helpstr.capitalize()} Help Commands",
            description=format_command_list(commands_dict[helpstr]),
            color=embed_color,
        )
        await interaction.response.send_message(embed=embed)
    else:
        # General help message if no specific category is chosen
        all_commands = {category: format_command_list(cmds) for category, cmds in commands_dict.items()}
        help_description = (
            f"{bot.description}\n\n**Member Commands**\n{all_commands['member commands']}\n\n"
            f"**TM Commands**\n{all_commands['tm commands']}\n\n"
        )
        embed = nextcord.Embed(
            title="Tishmish Help", description=help_description, color=embed_color
        )
        embed.add_field(
            name="View more options with `/help +1 options`",
            value="To use TM commands, server owner/admin can provide **tm** role to the member\n"
                  "[Help](https://github.com/awmie/tishmish/blob/main/readme.md)",
        )
        await interaction.response.send_message(embed=embed)



# T I S H M I S H commands
@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(name="ping", description="displays bot's latency")
async def ping_command(interaction: interactions.Interaction):
    em = nextcord.Embed(
        description=f"**Pong!**\n\n`{round(bot.latency*1000)}`ms", color=embed_color
    )
    await interaction.response.send_message(embed=em, delete_after=5)

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="role",
    description="sets an existing role which are below tishmish(role) for a user",
)
@commands.has_permissions(manage_roles=True)
async def set_role_command(interaction: interactions.Interaction, user: nextcord.Member, role: nextcord.Role):
    if role.position > interaction.guild.me.top_role.position:
        return await interaction.response.send_message("I do not have permission to manage this role.", ephemeral=True)
    if role.position > user.top_role.position:
        return await interaction.response.send_message("You do not have permission to manage this role.", ephemeral=True)
    await user.add_roles(role)
    embed = nextcord.Embed(
        description=f"`{user.name}` has been given a role called: **{role.name}**",
        color=embed_color
    )
    await interaction.response.send_message(embed=embed)

#checks for user connection to voice channels
async def user_connectivity(interaction: interactions.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("Join a voice channel first!")
        return False

    # Ensure the player has the necessary attributes
    vc: nextwave.Player = interaction.guild.voice_client
    if vc:
        if not hasattr(vc, "loop"):
            setattr(vc, "loop", False)

    return True  # Return True if user is connected

@bot.event
async def on_ready():
    print(f"logged in as: {bot.user.name}")
    bot.loop.create_task(node_connect())
    await bot.change_presence(
        activity=nextcord.Activity(type=nextcord.ActivityType.listening, name="/play")
    )


@bot.event
async def on_nextwave_node_ready(node: nextwave.Node):
    print(f"Node {node.identifier} connected successfully")


async def node_connect():
    await bot.wait_until_ready()
    await nextwave.NodePool.create_node(
        bot=bot,
<<<<<<< HEAD
        host=os.getenv('LAVALINK_HOST'),
        port=os.getenv('LAVALINK_PORT'),
        password=os.getenv('LAVALINK_PASSWORD'),
        https=os.getenv('LAVALINK_SECURE'),
        spotify_client=spotify.SpotifyClient(
            client_id=os.getenv('SPOTIFY_CLIENT_ID'),
            client_secret=os.getenv('SPOTIFY_CLIENT_SECRET'),
=======
        host=os.getenv("Host"),
        port=os.getenv("Port"),
        password=os.getenv("Password"),
        https=False,
        spotify_client=spotify.SpotifyClient(
            client_id=os.getenv("spotify_id"),
            client_secret=os.getenv("spotify_secret"),
>>>>>>> 8b882de (auto queue)
        ),
    )

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(name="info", description="shows information about the bot")
@commands.is_owner()
@commands.has_role("tm")
async def info_command(interaction: interactions.Interaction):
    await interaction.response.send_message(
        embed=nextcord.Embed(
            description=f"**Info**\ntotal server count: `{len(bot.guilds)}`",
            color=embed_color,
        )
    )


@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="loopqueue",
    description="loops the queue",
)
@commands.has_role("tm")
async def loopqueue_command(interaction: interactions.Interaction, type: str=nextcord.SlashOption(
    name="lq-options", description='options for loop queue', required=True, choices={"start","stop"}
)):
    vc: nextwave.Player = interaction.guild.voice_client
    if vc.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Unable to loop `QUEUE`, try adding more songs..",
                color=embed_color,
            )
        )
    if vc.lq == False and type == "start":
        vc.lq = True
        await interaction.response.send_message(
            embed=nextcord.Embed(
                description="**loopqueue**: `enabled`", color=embed_color
            )
        )
        try:
            if vc._source not in vc.queue:
                vc.queue.put(vc._source)
            else:
                """"""
        except Exception:
            return ""
    if vc.lq == True and type == "stop":
        vc.lq = False
        await interaction.response.send_message(
            embed=nextcord.Embed(
                description="**loopqueue**: `disabled`", color=embed_color
            )
        )
        if vc.queue.count == 1 and vc.queue._queue[0] == vc._source:
            del vc.queue._queue[0]
        else:
            return ""

@commands.cooldown(1, 1, commands.BucketType.user)
@bot.slash_command(name="play", description="Plays the provided track, YouTube playlist, or Spotify playlist link.")
async def play_command(interaction: nextcord.Interaction, *, search: str):
    # Check if the user is in a voice channel
    if not interaction.user.voice:
<<<<<<< HEAD
        return await interaction.response.send_message("Join a voice channel first!")
    elif not interaction.guild.voice_client:
        vc: nextwave.Player = await interaction.user.voice.channel.connect(
            cls=nextwave.Player
        )
    else:
        vc: nextwave.Player = interaction.guild.voice_client
    try:
        if search.startswith('https://open.spotify.com/playlist/') or search.startswith('https://open.spotify.com/album/') or search.startswith('https://open.spotify.com/track/'):
            await spotifyplay_command(interaction, search, limit=10)
            return
        if search.startswith('https://youtu.be/') or search.startswith('https://www.youtube.com/'):
            search = search.split("&")[0]
            search = search.split("?")[0]
            search = search.replace("https://youtu.be/","")
            search = search.replace("https://www.youtube.com/","")
            search = f"https://www.youtube.com/watch?v={search}"
    except Exception:
        return await interaction.response.send_message(embed=nextcord.Embed(description="Invalid Spotify URL", color=embed_color))
        
    search_results = await nextwave.tracks.YouTubeTrack.search(search)
    first_track = search_results[0] # Get the first track from the list
    if vc.queue.is_empty and vc.is_playing() is False:
        
        playString = await interaction.response.send_message(
            embed=nextcord.Embed(description="**searching...**", color=embed_color)
        )
        
        await vc.play(first_track)
=======
        return await interaction.followup.send("Join a voice channel first!", ephemeral=True)
>>>>>>> 8b882de (auto queue)

    # Connect to voice channel if the bot is not already connected
    vc: nextwave.Player = (
        interaction.guild.voice_client or await interaction.user.voice.channel.connect(cls=nextwave.Player)
    )

    # Defer the response to avoid timeout while processing
    await interaction.response.defer()

    # Check if the provided link is a Spotify or YouTube playlist link
    if search.startswith('https://open.spotify.com/'):
        await process_spotify_link(interaction, vc, search)
    elif "playlist?list=" in search:  # Detect YouTube playlist links
        await process_youtube_playlist(interaction, vc, search)
    else:
<<<<<<< HEAD
        await vc.queue.put_wait(first_track)
        await interaction.send(
            embed=nextcord.Embed(
                description=f"Added to the `QUEUE`\n\n`{first_track.title}`",
                color=embed_color,
            )
        )

        # await added_to_queue_msg.edit(embed=nextcord.Embed(description=f"Added to the `QUEUE`\n\n`{first_track.title}`", color=embed_color), delete_after=5)
    
    setattr(vc, "loop", False)
    user_dict[first_track.identifier] = interaction.user.mention
=======
        # If not a Spotify or YouTube playlist link, proceed with YouTube track or search query
        await process_youtube_or_search(interaction, vc, search)


async def process_spotify_link(interaction: nextcord.Interaction, vc: nextwave.Player, search: str, limit: int = 30):
    """Handles Spotify links within the play command."""
    try:
        # Initialize the embed for queue status
        queue_embed = nextcord.Embed(
            title="Spotify Playlist Processing",
            description="Initializing the **QUEUE**...",
            color=embed_color
        )
        queue_completion = await interaction.followup.send(embed=queue_embed)

        # Fetch tracks from Spotify playlist or album
        processed_tracks = 0  # Track number of processed songs
        async for partial in spotify.SpotifyTrack.iterator(
            query=search,
            type=spotify.SpotifySearchType.playlist,  # Modify to handle albums if needed
            partial_tracks=True,
            limit=limit
        ):
            # Search for corresponding YouTube tracks
            youtube_tracks = await nextwave.tracks.YouTubeTrack.search(partial.title)
            if not youtube_tracks:
                continue  # Skip if no YouTube track is found for this Spotify track

            youtube_track = youtube_tracks[0]
            user_dict[youtube_track.identifier] = interaction.user.mention  # Track who queued this song

            # If nothing is playing, start playing the track immediately
            if vc.queue.is_empty and not vc.is_playing():
                await vc.play(youtube_track)
            else:
                # Otherwise, queue the track
                await vc.queue.put_wait(youtube_track)

            # Update track processing count
            processed_tracks += 1
            if processed_tracks >= limit:
                break  # Stop if we've hit the song limit

            # Update the embed with progress
            queue_embed.description = f"Added song no. `{processed_tracks}` to the **QUEUE**. Remaining: `{limit - processed_tracks}`."
            await queue_completion.edit(embed=queue_embed)

        # Final embed update with total added songs
        queue_embed.description = f"**Finished!** Successfully added `{processed_tracks}` songs to the **QUEUE**."
        await queue_completion.edit(embed=queue_embed)

        # Ensure the loop mode is disabled
        setattr(vc, "loop", False)

    except spotify.SpotifyRequestError as e:
        # Handle any errors with the Spotify API
        error_embed = nextcord.Embed(
            description=f"Failed to fetch tracks from Spotify. Error: {e}",
            color=embed_color
        )
        await interaction.followup.send(embed=error_embed)

    except Exception as e:
        # Catch any other unexpected errors
        error_embed = nextcord.Embed(
            description=f"An unexpected error occurred: {str(e)}",
            color=embed_color
        )
        await interaction.followup.send(embed=error_embed)


async def process_youtube_playlist(interaction: nextcord.Interaction, vc: nextwave.Player, playlist_url: str):
    """Handles YouTube playlist links within the play command using Nextcord's nextwave."""
    try:
        # Initialize the embed for queue status
        queue_embed = nextcord.Embed(
            title="YouTube Playlist Processing",
            description="Fetching playlist tracks...",
            color=embed_color
        )
        queue_completion = await interaction.followup.send(embed=queue_embed)

        # Fetch YouTube playlist using nextwave
        youtube_playlist = await nextwave.tracks.YouTubePlaylist.search(playlist_url)

        # Access the tracks from the YouTubePlaylist object
        playlist_tracks = youtube_playlist.tracks
        processed_tracks = 0

        for youtube_track in playlist_tracks:
            user_dict[youtube_track.identifier] = interaction.user.mention  # Track who queued this song

            # If nothing is playing, start playing the track immediately
            if vc.queue.is_empty and not vc.is_playing():
                await vc.play(youtube_track)
            else:
                # Otherwise, queue the track
                await vc.queue.put_wait(youtube_track)

            # Update track processing count
            processed_tracks += 1

            # Update the embed with progress
            queue_embed.description = f"Added song no. `{processed_tracks}` to the **QUEUE** from the playlist."
            await queue_completion.edit(embed=queue_embed)

        # Final embed update with total added songs
        queue_embed.description = f"**Finished!** Successfully added `{processed_tracks}` tracks from the playlist to the **QUEUE**."
        await queue_completion.edit(embed=queue_embed)

        # Ensure the loop mode is disabled
        setattr(vc, "loop", False)

    except Exception as e:
        error_embed = nextcord.Embed(
            description=f"Failed to process the YouTube playlist. Error: {str(e)}",
            color=embed_color
        )
        await interaction.followup.send(embed=error_embed)




async def process_youtube_or_search(interaction: nextcord.Interaction, vc: nextwave.Player, search: str):
    """Handles YouTube track or search queries within the play command."""
    try:
        # Process YouTube link or general search query
        if search.startswith('https://youtu.be/') or search.startswith('https://www.youtube.com/'):
            search = search.split("&")[0].split("?")[0].replace("https://youtu.be/", "").replace("https://www.youtube.com/", "")
            search = f"https://www.youtube.com/watch?v={search}"

        search_results = await nextwave.tracks.YouTubeTrack.search(search)
        if not search_results:
            return await interaction.followup.send("No results found for your search.", ephemeral=True)

        first_track = search_results[0]  # Get the first track from the search results

        # If nothing is currently playing, start playing the track
        if vc.queue.is_empty and not vc.is_playing():
            await vc.play(first_track)
            await interaction.followup.send(embed=nextcord.Embed(description=f"**Now playing:** `{first_track.title}`", color=embed_color))
        else:
            # Otherwise, add it to the queue
            await vc.queue.put_wait(first_track)
            await interaction.followup.send(embed=nextcord.Embed(description=f"Added to the `QUEUE`\n`{first_track.title}`", color=embed_color))

        setattr(vc, "loop", False)
        user_dict[first_track.identifier] = interaction.user.mention

    except Exception as e:
        await interaction.followup.send(
            embed=nextcord.Embed(description="An error occurred while processing your request.", color=embed_color)
        )


>>>>>>> 8b882de (auto queue)
@bot.event
async def on_nextwave_track_end(player: nextwave.Player, track: nextwave.Track, reason):
    # Get the voice client (player)
    vc: nextwave.Player = player.guild.voice_client

    # Ensure the player has a loop attribute; if not, set it to False
    if not hasattr(vc, 'loop'):
        setattr(vc, 'loop', False)

    # Check if looping is enabled, if so, replay the same track
    if vc.loop is True:
        return await player.play(track)

    try:
        # If the queue is not empty, play the next song
        if not player.queue.is_empty:
            # Custom property for looping the queue if lq is True
            if hasattr(player, 'lq') and player.lq:
                player.queue.put(player.queue._queue[0])  # Requeue the first track
            next_song = player.queue.get()
            await player.play(next_song)

            # Announce the next song in the text channel
            channel = player.channel
            await channel.send(
                embed=nextcord.Embed(
<<<<<<< HEAD
                    description=f"**Now playing from the queue:**\n\n`{next_song.title}`",color=embed_color,
                    ),
                delete_after=player.track.length
                )
=======
                    description=f"**Now playing from the queue:**\n\n`{next_song.title}`",
                    color=embed_color
                ),
                delete_after=player.track.length  # Auto delete after song length
            )
            await asyncio.sleep(2)
            await update_queue(interaction)
>>>>>>> 8b882de (auto queue)
        else:
            # If the queue is empty, stop the player
            await player.stop()
            channel = player.channel
            await channel.send(
                embed=nextcord.Embed(
                    description="The queue is empty.",
                    color=embed_color
                ),
                delete_after=5
            )
            await asyncio.sleep(2)
            await update_queue(interaction)

    except Exception as e:
        # Handle any errors that occur while playing the next song
        channel = player.channel
        await channel.send(
            embed=nextcord.Embed(
<<<<<<< HEAD
                description="An error occurred while playing the next song.", color=embed_color
            ),delete_after=5
        ) 


@commands.cooldown(1, 1, commands.BucketType.user)
@bot.slash_command(
    name="spotifyplay",
    description="plays the provided spotify playlist link up to the provided song number",
)
async def spotifyplay_command(
    interaction: interactions.Interaction, search: str, limit: int = 100
):
    if not interaction.user.voice:
        return await interaction.response.send_message("Join a voice channel first!")

    vc: nextwave.Player = (
        interaction.guild.voice_client
        or await interaction.user.voice.channel.connect(cls=nextwave.Player)
    )

    try:
        # Initialize the embed before the loop
        queue_embed = nextcord.Embed(
            description="initializing the **QUEUE**...", color=embed_color
        )
        queue_completion = await interaction.response.send_message(embed=queue_embed)
        
        # Iterate over the tracks
        async for partial in spotify.SpotifyTrack.iterator(
            query=search,
            type=spotify.SpotifySearchType.playlist,
            partial_tracks=True,
            limit=limit,
        ):
            # Search for YouTubeTrack using the title
            youtube_tracks = await nextwave.tracks.YouTubeTrack.search(partial.title)
            if not youtube_tracks:
                continue  # Skip if no YouTube track is found

            youtube_track = youtube_tracks[0]
            user_dict[youtube_track.identifier] = interaction.user.mention

            if vc.queue.is_empty and vc.is_playing() is False:
                await vc.play(youtube_track)
                limit -= 1
            else:
                await vc.queue.put_wait(youtube_track)
            
            # Update the embed description with the current status
            if limit == 100:
                queue_embed.description = f"Song no. `1` added to the track and remaining are being pushed to the **QUEUE**:`{vc.queue.count}/100`"
            else:
                queue_embed.description = f"Song no. `{100 - limit + 1}` added to the track and remaining are being pushed to the **QUEUE**:`{vc.queue.count}/{limit}`"
            await queue_completion.edit(embed=queue_embed)

        setattr(vc, "loop", False)

        queue_embed.description = f"Total successfully added to the **QUEUE**: `{vc.queue.count}`"
        await queue_completion.edit(embed=queue_embed)

    except spotify.SpotifyRequestError as e:
        await interaction.response.send_message(
            embed=nextcord.Embed(description=f"{e}", color=embed_color)
        )
=======
                description=f"An error occurred while playing the next song: {str(e)}",
                color=embed_color
            ),
            delete_after=5
        )
>>>>>>> 8b882de (auto queue)

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(name="pause", description="pauses the current playing track")
async def pause_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client

    if vc._source:
        if not vc.is_paused():
            await vc.pause()
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description="`PAUSED` the music!", color=embed_color
                ),delete_after=5
            )

        elif vc.is_paused():
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description="Already in `PAUSED State`", color=embed_color
                ),delete_after=5
            )
    else:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Player is not `playing`!", color=embed_color
            ),delete_after=5
        )


@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(name="resume", description="resumes the paused track")
async def resume_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client

    if vc.is_playing():
        if vc.is_paused():
            await vc.resume()
            await interaction.response.send_message(
                embed=nextcord.Embed(description="Music `RESUMED`!", color=embed_color),delete_after=5
            )

        elif vc.is_playing():
            await interaction.response.send_message(
                embed=nextcord.Embed(
                    description="Already in `RESUMED State`", color=embed_color
                ),delete_after=5
            )
    else:
        await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Player is not `playing`!", color=embed_color
            ),delete_after=5
        )


@commands.cooldown(1, 2, commands.BucketType.user)
<<<<<<< HEAD
@bot.slash_command(name="skip", description="skips to the next track")
@commands.has_role("tm")
async def skip_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client

    if vc.loop == True:
        vclooptxt = "Disable the `LOOP` mode to skip\n**/loop** again to disable the `LOOP` mode\nAdding songs disables the `LOOP` mode"
        return await interaction.response.send_message(
            embed=nextcord.Embed(description=vclooptxt, color=embed_color),delete_after=5
        )

    elif vc.queue.is_empty:
        await vc.stop()
        await vc.resume()
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Song stopped! No songs in the `QUEUE`",
                color=embed_color,
            ),delete_after=5
        )

    else:
        await vc.stop()
        vc.queue._wakeup_next()
        await vc.resume()
        await interaction.response.send_message(
            embed=nextcord.Embed(description="`SKIPPED`!", color=embed_color),delete_after=5
        )
        await queue_command(interaction)

@commands.cooldown(1, 2, commands.BucketType.user)
=======
>>>>>>> 8b882de (auto queue)
@bot.slash_command(
    name="disconnect",
    description="disconnects the player from the vc",
)
@commands.has_role("tm")
async def disconnect_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client
    try:
        await vc.stop()
        await vc.resume()
        vc.queue._queue.clear()
        await vc.disconnect(force=True)
        await interaction.response.send_message(
            embed=nextcord.Embed(
                description="**BYE!** Have a great time!", color=embed_color
            )
        )
    except Exception:
        await interaction.response.send_message(
            embed=nextcord.Embed(description="Failed to destroy!", color=embed_color),delete_after=5
        )

@bot.event
async def on_voice_state_update(member, before, after):
    if (
        before.channel is not None
        and (bot.user in before.channel.members and len(before.channel.members) == 1)
        or (member.id == bot.user.id and after.channel is None)
    ):
        for vc in bot.voice_clients:
            if vc.channel == before.channel:
                # Find the first text channel in the same category as the voice channel
                category = before.channel.category
                text_channel = None

                if category:
                    # Find a text channel in the same category
                    for channel in category.channels:
                        if isinstance(channel, nextcord.TextChannel):
                            text_channel = channel
                            break

                # Fallback to the guild's system channel if no text channel found in the same category
                if not text_channel:
                    text_channel = member.guild.system_channel

                if text_channel is not None:
                    embed = nextcord.Embed(
                        description="Auto-disconnecting due to all participants leaving the voice channel.",
                        color=embed_color
                    )
                    await text_channel.send(embed=embed)

                await asyncio.sleep(5)
                await vc.stop()
                await vc.disconnect(force=True)
                break

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="nowplaying",
    description="shows the current track information",
)
async def nowplaying_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client
    if not vc.is_playing():
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Not playing anything!", color=embed_color)
        )

    # vcloop conditions
    loopstr = "enabled" if vc.loop else "disabled"
    state = "paused" if vc.is_paused() else "playing"
    """numpy array usertag indexing"""
    global user_list
    user_list = list(user_dict.items())
    user_arr = np.array(user_list)
    song_index = np.flatnonzero(
        np.char.find(user_arr, vc.track.identifier) == 0
    )

    if len(song_index) == 0:
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Song not found", color=embed_color)
        )

    # Extract the first index from song_index array
    arr_index = int(song_index[0] / 2)

    requester = user_arr[arr_index, 1]

    nowplaying_description = (
        f"[`{vc.track.title}`]({str(vc.track.uri)})\n\n**Requested by**: {requester}"
    )
    em = nextcord.Embed(
        description=f"**Now Playing**\n\n{nowplaying_description}", color=embed_color
    )
    em.add_field(
        name="**Song Info**",
        value=f"• Author: `{vc.track.author}`\n• Duration: `{str(datetime.timedelta(seconds=vc.track.length))}`",
    )
    em.add_field(
        name="**Player Info**",
        value=f"• Player Volume: `{vc.volume}`\n• Loop: `{loopstr}`\n• Current State: `{state}`",
        inline=False,
    )

    return await interaction.response.send_message(embed=em, delete_after=10)


@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="loop",
    description="loop / exitloop",
)
@commands.has_role("tm")
async def loop_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client
    if not vc._source:
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="No song to `loop`", color=embed_color),delete_after=5
        )
    try:
        vc.loop ^= True
    except Exception:
        setattr(vc, "loop", False)
    return (
        await interaction.response.send_message(
            embed=nextcord.Embed(description="**LOOP**: `enabled`", color=embed_color),delete_after=5
        )
        if vc.loop
        else await interaction.response.send_message(
            embed=nextcord.Embed(description="**LOOP**: `disabled`", color=embed_color), delete_after=5
        )
    )

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="queue",
    description="Displays the current queue",
)
async def queue_command(interaction: interactions.Interaction):
    vc: nextwave.Player = interaction.guild.voice_client

    if await user_connectivity(interaction) == False:
        return

    if vc.queue.is_empty:
        # If the queue is empty, send an embed that shows "empty"
        await interaction.response.send_message(
            embed=nextcord.Embed(description="**QUEUE**\n\n`empty`", color=embed_color)
        )
        return

    # Create the current queue embed from the nextwave queue
    global queue_embed
    queue_embed = nextcord.Embed(
        description="**QUEUE**\n\n" + "\n".join(
            f"`{i+1}` {song.title if isinstance(song, nextwave.tracks.PartialTrack) else song.info['title']}"
            for i, song in enumerate(vc.queue, start=0)
        ),
        color=embed_color
    )

    # Send the current queue in a new message
    await interaction.response.send_message(embed=queue_embed)


async def update_queue(interaction):
    vc = interaction.guild.voice_client  # Get the current voice client from interaction

    if vc is None or vc.queue.is_empty:
        # If no voice client or queue is empty, we can just send an empty queue message to the same channel
        await interaction.channel.send(
            embed=nextcord.Embed(description="**QUEUE**\n\n`empty`", color=embed_color)
        )
        return

    # Create the updated queue embed directly from the nextwave queue
    updated_embed = nextcord.Embed(
        description="**QUEUE**\n\n" + "\n".join(
            f"`{i+1}` {song.title if isinstance(song, nextwave.tracks.PartialTrack) else song.info['title']}"
            for i, song in enumerate(vc.queue, start=0)
        ),
        color=embed_color
    )

    # Send the updated queue as a new message in the same channel
    await interaction.channel.send(embed=updated_embed)



@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="skip",
    description="skips the current song",
)
@commands.has_role("tm")
async def skip_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return

    # Defer the response to avoid interaction expiry
    await interaction.response.defer()

    vc: nextwave.Player = interaction.guild.voice_client

    # Skip the current song
    if vc.loop == True:
        vclooptxt = "Disable the `LOOP` mode to skip\n**/loop** again to disable the `LOOP` mode\nAdding songs disables the `LOOP` mode"
        return await interaction.followup.send(
            embed=nextcord.Embed(description=vclooptxt, color=embed_color), delete_after=5
        )

    elif vc.queue.is_empty:
        await vc.stop()
        await vc.resume()
        return await interaction.followup.send(
            embed=nextcord.Embed(
                description="Song stopped! No songs in the `QUEUE`",
                color=embed_color,
            ), delete_after=5
        )

    else:
        await vc.stop()
        vc.queue._wakeup_next()
        await vc.resume()
        await interaction.followup.send(
            embed=nextcord.Embed(description="`SKIPPED`!", color=embed_color), delete_after=5
        )
        await asyncio.sleep(2)
        await update_queue(interaction)

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="shuffle",
    description="shuffles the existing queue randomly",
)
@commands.has_role("tm")
async def shuffle_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client
    if vc.queue.count > 1:
        vc.queue.shuffle()
        await interaction.response.send_message(
            embed=nextcord.Embed(description="Shuffled the `QUEUE`", color=embed_color),delete_after=5
        )
        await asyncio.sleep(2)
        return await update_queue(interaction)
    elif vc.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="`QUEUE` is empty", color=embed_color),delete_after=5
        )
    else:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="`QUEUE` has less than `3 songs`",
                color=embed_color,
            ),delete_after=5
        )



@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="del",
    description="deletes the specified track",
)
@commands.has_role("tm")
async def del_command(interaction: interactions.Interaction, position: int):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client
    if vc.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="No songs in the `QUEUE`", color=embed_color
            ),delete_after=5
        )
    if position <= 0:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Position can not be `ZERO`* or `LESSER`",
                color=embed_color,
            ),delete_after=5
        )
    elif position > vc.queue.count:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"Position `{position}` is outta range", color=embed_color
            ),delete_after=5
        )
    else:
        SongToBeDeleted = vc.queue._queue[position - 1].title
        del vc.queue._queue[position - 1]
        await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"`{SongToBeDeleted}` removed from the QUEUE",
                color=embed_color,
            ),delete_after=5
        )
        await asyncio.sleep(2)
        return await update_queue(interaction)


@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="skipto",
    description="skips to the specified track",
)
@commands.has_role("tm")
async def skipto_command(interaction: interactions.Interaction, position: int):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client
    if vc.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="No songs in the `QUEUE`", color=embed_color
            ),delete_after=5
        )
    if position <= 0:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Position can not be `ZERO`* or `LESSER`",
                color=embed_color,
            ),delete_after=5
        )
    elif position > vc.queue.count:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"Position `{position}` is outta range", color=embed_color
            ),delete_after=5
        )
    elif position == vc.queue._queue[position - 1]:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Already in that `Position`!", color=embed_color
            ),delete_after=5
        )
    else:
        vc.queue.put_at_front(vc.queue._queue[position - 1])
        del vc.queue._queue[position]
        return await skip_command(interaction)


@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="move",
    description="moves the track to the specified position",
)
@commands.has_role("tm")
async def move_command(
    interaction: interactions.Interaction, song_position: int, move_position: int
):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client
    if vc.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="No songs in the `QUEUE`!", color=embed_color
            ),delete_after=5
        )
    if song_position <= 0 or move_position <= 0:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Position can not be `ZERO`* or `LESSER`",
                color=embed_color,
            ),delete_after=5
        )

    queue_length = len(vc.queue)
    if song_position > queue_length or move_position > queue_length:
        position = song_position if song_position > queue_length else move_position
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"Position `{position}` is outta range!", color=embed_color
            ),delete_after=5
        )
    elif song_position == move_position:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"Already in that `Position`:{move_position}",
                color=embed_color,
            ),delete_after=5
        )
    else:
        move_song = vc.queue._queue[song_position - 1]
        vc.queue._queue.remove(move_song)
        move_index = move_position - 1
        vc.queue.put_at_index(move_index, move_song)

        moved_song_name = move_song.title
        await interaction.response.send_message(
            embed=nextcord.Embed(
                description=f"**{moved_song_name}** moved at Position:`{move_position}`",
                color=embed_color,
            ),delete_after=5
        )
        await asyncio.sleep(2)
        return await update_queue(interaction)

@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(name="volume", description="sets the volume")
@commands.has_role("tm")
async def volume_command(interaction: interactions.Interaction, playervolume: int):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client
    if vc.is_connected():
        if playervolume > 100:
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description="**VOLUME** supported upto `100%`", color=embed_color
                ),delete_after=5
            )
        elif playervolume < 0:
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description="**VOLUME** can not be `negative`", color=embed_color
                ),delete_after=5
            )
        else:
            await interaction.response.send_message(
                embed=nextcord.Embed(
                    description=f"**VOLUME**\nSet to `{playervolume}%`",
                    color=embed_color,
                ),delete_after=5
            )
            return await vc.set_volume(playervolume)
    elif not vc.is_connected():
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Player not connected!", color=embed_color),delete_after=5
        )



@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(name="restart", description="restarts the song")
@commands.has_role("tm")
async def restart_command(interaction: interactions.Interaction):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client
    if not vc.is_playing():
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Player not playing!", color=embed_color),delete_after=5
        )
    elif vc.is_playing():
        msg = await interaction.response.send_message(embed=nextcord.Embed(description="Restarting...", color=embed_color))
        await vc.seek(0)
        return await msg.edit(embed=nextcord.Embed(description="Player restarted!", color=embed_color),delete_after=5)



@commands.cooldown(1, 5, commands.BucketType.user)
@bot.slash_command(
    name="clear", description="clears the queue"
)
@commands.has_role("tm")
async def clear_command(interaction: interactions.Interaction):
    vc: nextwave.Player = interaction.guild.voice_client
    if await user_connectivity(interaction) == False:
        return
    if vc.queue.is_empty:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="No `SONGS` are present", color=embed_color
            ),delete_after=5
        )
    vc.queue._queue.clear()
    vc.lq = False
    clear_command_embed = nextcord.Embed(
        description="`QUEUE` cleared", color=embed_color
    )
    return await interaction.response.send_message(embed=clear_command_embed, delete_after=5)


@commands.cooldown(1, 2, commands.BucketType.user)
@bot.slash_command(
    name="save",
    description="dms the current or specified song to the user",
)
async def save_command(interaction: interactions.Interaction):
    vc: nextwave.Player = interaction.guild.voice_client
    if await user_connectivity(interaction) == False:
        return
    user = await bot.fetch_user(interaction.user.id)
    if vc._source:
        await user.send(
            embed=nextcord.Embed(description=f"`{vc._source}`", color=embed_color)
        )
        song_saved = await interaction.response.send_message(
            embed=nextcord.Embed(description="**SONG** saved!", color=embed_color),delete_after=5
        )
        await song_saved.delete(delay=5)

    else:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="There is no `song` | `queue` available", color=embed_color
            ),delete_after=5
        )

@commands.cooldown(1,2,commands.BucketType.user)
@bot.slash_command(name="seek", description="seeks to the specified position for eg. 30sec")
@commands.has_role("tm")
async def seek_command(interaction:interactions.Interaction, seekpos: int):
    if await user_connectivity(interaction) == False:
        return
    vc: nextwave.Player = interaction.guild.voice_client
    if not vc.is_playing():
        return await interaction.response.send_message(
            embed=nextcord.Embed(description="Player not playing!", color=embed_color),delete_after=5
        )

    else:
        if seekpos < 0 or seekpos > vc.track.length:
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description=f"SEEK length `{seekpos}` outta range",
                    color=embed_color
                ),delete_after=5
            )
        else:
            await vc.seek(seekpos*1000)
            return await interaction.response.send_message(
                embed=nextcord.Embed(
                    description=f"Player seeked to `{seekpos}` sec.",
                    color=embed_color
                ),delete_after=5
            )

<<<<<<< HEAD
#autoplay command development
from g4f.client import Client
# from g4f.Provider import HuggingChat
import nest_asyncio
nest_asyncio.apply()

client = Client()
    # Get the seed for prediction

=======

from g4f.client import Client
#from groq import Groq
import nest_asyncio
nest_asyncio.apply()

# Initialize Groq client
client = Client()
>>>>>>> 8b882de (auto queue)

@commands.cooldown(1, 5, commands.BucketType.user)
@bot.slash_command(name="predict", description="Predict and add songs to the queue")
@commands.has_role("tm")
async def predict_command(interaction: nextcord.Interaction, num_songs: int):
    if num_songs < 3 or num_songs > 10:
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="Please enter a number between 3 to 10 for predictions.",
                color=embed_color
            ),
            delete_after=5
        )

    vc: nextwave.Player = interaction.guild.voice_client

    if vc is None or not vc.is_connected():
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="The bot is not connected to a voice channel.",
                color=embed_color
            ),
            delete_after=5
        )

    if vc.queue.is_empty and not vc.is_playing():
        return await interaction.response.send_message(
            embed=nextcord.Embed(
                description="There's nothing currently playing or in the queue to base predictions on.",
                color=embed_color
            ),
            delete_after=5
        )

    # Defer the response to avoid timeout
    await interaction.response.defer()
<<<<<<< HEAD
=======

>>>>>>> 8b882de (auto queue)
    # Get the seed for prediction
    if vc.queue.is_empty:
        seed_song = vc.track.title
    else:
<<<<<<< HEAD
        seed_song = f"{vc.queue} {vc.track.title}"

    # Ask the AI to predict multiple songs at once
    chat_completion = client.chat.completions.create(
        # model="CodeLlama-70b-Instruct-hf",  # Ensure you use the correct model
        model="gpt-4",
        messages=[{"role": "user", "content": f'predict the next {num_songs} number of songs based on this list of song/s for the user, do not send long texts, here is the list - {seed_song}, IMPORTANT remember to seperate each preidicted songs by this ===='}]
    )
    
    response = chat_completion.choices[0].message.content
    predicted_songs = response.split("====")

    # Loop through each predicted song and use the play_command to add it to the queue
    # print(seed_song)
    # print(response)
    # print(predicted_songs)
    
    for songs in predicted_songs:
        # print(songs)
        await play_command(interaction, search=songs)
=======
        seed_song = f"{vc.track.title},{vc.queue}"

    # Use Groq for song prediction
    # Use Groq for song prediction
    chat_completion = client.chat.completions.create(
        model="",
        messages=[
            {
                "role": "user",
                "content": (
                    f"Based on the following list of Gen Z songs provided by the user, predict {num_songs} additional popular Gen Z songs. "
                    "Do not ask for additional seed songs; instead, use the provided list to make your predictions. "
                    "Only include the predicted song titles, separated by newlines, with no additional explanations or formatting. "
                    f"The starting list of songs is: {seed_song}."
                )
            }
        ]
    )

    response = chat_completion.choices[0].message.content

    # Split the response on newline and clean it up
    predicted_songs = [
        song.strip() for song in response.splitlines() if song.strip()
    ]

    # Debugging output
    print(seed_song)
    print(response)
    print(predicted_songs)

    # Add each predicted song to the queue using the play_command
    for song in predicted_songs:
        await process_youtube_or_search(interaction,vc, song)  # Ensure song is stripped of excess whitespace
>>>>>>> 8b882de (auto queue)

    # Send the final confirmation message after all songs have been added
    await interaction.followup.send(
        embed=nextcord.Embed(
            description=f"Added {num_songs} predicted songs to the queue.",
            color=embed_color
        ),
        delete_after=10
    )


<<<<<<< HEAD

"""main"""

if __name__ == "__main__":
    bot.run(os.getenv("TOKEN"))
=======
"""main"""

if __name__ == "__main__":
    bot.run(os.getenv("tishmish_token"))
>>>>>>> 8b882de (auto queue)
