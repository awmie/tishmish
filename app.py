
import datetime, random
import nextcord
from nextcord.ext import commands
import wavelink
from wavelink.ext import spotify
import os
#intents
intents = nextcord.Intents(messages = True, guilds = True)
intents.guild_messages = True
intents.members = True
intents.message_content = True
intents.voice_states = True
intents.emojis_and_stickers = True
all_intents = intents.all()
all_intents= True

bot = commands.Bot(command_prefix=',', intents = intents, description='Premium quality music bot for free!')
global user_list
user_list= []
setattr(wavelink.Player, 'lq', False)

class help_command(commands.MinimalHelpCommand):
    async def send_pages(self):
        destination = self.get_destination()
        for page in self.paginator.pages:
            em = nextcord.Embed(description=page, color=nextcord.Color.from_rgb(128, 67, 255))
            await destination.send(embed=em)
            
bot.help_command = help_command(no_category='Tishmish help-commands\n')

@bot.command(name='ping', help=f"displays bot's latency")
async def ping(ctx):    
    em = nextcord.Embed(title="pong!", description=f'{round(bot.latency*1000)}ms', color=ctx.author.color)
    await ctx.send(embed=em)

async def user_connectivity(ctx: commands.Context):
    if not getattr(ctx.author.voice, 'channel', None):
        await ctx.send(embed=nextcord.Embed(description=f'try after joining *voice channel..*', color=ctx.author.color))        
        return False
    else:   
        return True

@bot.event
async def on_ready():
    print(f'logged in as: {bot.user.name}')
    bot.loop.create_task(node_connect())
    await bot.change_presence(activity=nextcord.Activity(type=nextcord.ActivityType.listening, name=",help"))

@bot.event
async def on_wavelink_node_ready(node: wavelink.Node):
    print(f'Node {node.identifier} connected successfully')

async def node_connect():
    await bot.wait_until_ready()
    await wavelink.NodePool.create_node(bot=bot, host='jp-lava.islantay.tk', port=443, password='AmeliaWatsonisTheBest**!', https=True)

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=nextcord.Embed(description="missing *arguments..*", color=ctx.author.color))


@bot.event
async def on_wavelink_track_end(player: wavelink.Player, track: wavelink.Track, reason):
    ctx = player.ctx
    vc: player = ctx.voice_client
    
    if vc.loop:
        return await vc.play(track)

    try:
        if not vc.queue.is_empty:
            if vc.lq:
                vc.queue.put(vc.queue._queue[0])
            next_song = vc.queue.get()
            await vc.play(next_song)
            return await ctx.send(embed=nextcord.Embed(description=f'**current *song* playing from the *queue* **\n*{next_song.title}*', color=ctx.author.color))
    except:
        await vc.stop()
        return await ctx.send(embed=nextcord.Embed(description=f'no *songs* in the queue', color=ctx.author.color))

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandOnCooldown):
        em = nextcord.Embed(description=f'**cooldown active**\ntry again in *{error.retry_after:.2f}s*',color=ctx.author.color)
        await ctx.send(embed=em)
        
@commands.cooldown(1, 2, commands.BucketType.user)        
@bot.command(name='loopqueue', aliases=['lq'], help='loops the existing queue')
async def loopqueue_command(ctx: commands.Context, type:str):
    vc: wavelink.Player = ctx.voice_client
    if not vc.queue.is_empty:
        
        if type == 'start':
            vc.lq = True
        if type == 'stop' and vc.lq is True:
            vc.lq = False
        
        if type != 'start' and type != 'stop':
            return await ctx.send(embed=nextcord.Embed(description='Use:\n**,lq start**---> *starts* the loopqueue\n**,lq stop**---> *stops* the loopqueue', color=ctx.author.color))

        if vc.lq is True:
            return await ctx.send(embed=nextcord.Embed(title='Loop Queue', description='*enabled*', color=ctx.author.color))
        elif vc.lq is False:
            return await ctx.send(embed=nextcord.Embed(title='Loop Queue', description='*disabled*', color=ctx.author.color))
    else:
        return await ctx.send(embed=nextcord.Embed(title='Queue', description='No *songs* are addded', color=ctx.author.color))

@commands.cooldown(1, 1, commands.BucketType.user)  
@bot.command(name='play', aliases=['p'], help='plays the given track provided by the user')
async def play_command(ctx: commands.Context, *, search: wavelink.YouTubeTrack):

    if not getattr(ctx.author.voice, 'channel', None):
        return await ctx.send(embed=nextcord.Embed(description=f'Try after joining *voice channel..*', color=ctx.author.color))        
    elif not ctx.voice_client:
        vc: wavelink.Player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
    else:
        vc: wavelink.Player = ctx.voice_client

    if vc.queue.is_empty and vc.is_playing() is False:    
        await vc.play(search)
        playString = nextcord.Embed(title='song found:', description=f'*{search.title}*', color=ctx.author.color)
        await ctx.send(embed=playString)

    else:
        await vc.queue.put_wait(search)
        await ctx.send(embed=nextcord.Embed(title='added to the queue:', description=f'*{search.title}*', color=ctx.author.color))
        
    vc.ctx = ctx 

    setattr(vc, 'loop', False)

    user_dict = {}
    user_dict[search.title] =ctx.author._user.name
    user_list.append(user_dict)

@commands.cooldown(1, 2, commands.BucketType.user)  
@bot.command(name='pause', aliases=['stop'], help='pauses the current playing track')
async def pause_command(ctx: commands.Context):
    if await user_connectivity(ctx) == False:
        return
    else:
        vc: wavelink.Player = ctx.voice_client

        if vc.is_playing():
            if not vc.is_paused():
                await vc.pause()
                await ctx.send(embed=nextcord.Embed(description='*paused* the music!', color=ctx.author.color))

            elif vc.is_paused():
                await ctx.send(embed=nextcord.Embed(description='already *paused*', color=ctx.author.color))
        else:
            await ctx.send(embed=nextcord.Embed(description='player is *not playing!*', color=ctx.author.color))

@commands.cooldown(1, 2, commands.BucketType.user)  
@bot.command(name='resume', help='resumes the paused track')
async def resume_command(ctx: commands.Context):
    if await user_connectivity(ctx) == False:
        return
    else:
        vc: wavelink.Player = ctx.voice_client

        if vc.is_playing():
            if vc.is_paused():
                await vc.resume()
                await ctx.send(embed=nextcord.Embed(description='music *resumed!*', color=ctx.author.color))

            elif vc.is_playing():
                await ctx.send(embed=nextcord.Embed(description='already *playing* music!', color=ctx.author.color))
        else:
            await ctx.send(embed=nextcord.Embed(description='player is *not playing!*', color=ctx.author.color))
        
@commands.cooldown(1, 2, commands.BucketType.user)  
@bot.command(name='skip', aliases=['next', 's'], help='skips to the next track')
async def skip_command(ctx: commands.Context):
    if await user_connectivity(ctx) == False:
        return
    else:
        vc: wavelink.Player = ctx.voice_client

        if vc.loop == True:
            return await ctx.send(embed=nextcord.Embed(description=f'To move to the next song disable the *loop mode*', color=ctx.author.color))

        elif vc.queue.is_empty:
            await vc.stop()
            await vc.resume()
            return await ctx.send(embed=nextcord.Embed(description=f'Song stopped! No *songs* are *queued*', color=ctx.author.color))

        else:
            await vc.stop()
            vc.queue._wakeup_next()
            await vc.resume()
            return await ctx.send(embed=nextcord.Embed(description=f'*skipped!*', color=ctx.author.color))

@commands.cooldown(1, 2, commands.BucketType.user)  
@bot.command(name='disconnect', aliases=['dc', 'leave'], help='disconnects the player from the vc')
async def disconnect_command(ctx: commands.Context):
    if await user_connectivity(ctx) == False:
        return
    else:
        vc : wavelink.Player = ctx.voice_client
        try:
            await vc.disconnect()
            await ctx.send(embed=nextcord.Embed(description='player *destroyed!*', color=ctx.author.color))
        except Exception:
            await ctx.send(embed=nextcord.Embed(description='failed to *destroy!*', color=ctx.author.color))
            
@commands.cooldown(1, 2, commands.BucketType.user)  
@bot.command(name='nowplaying', aliases=['np'], help='shows the current track information')
async def nowplaying_command(ctx: commands.Context):
    if await user_connectivity(ctx) == False:
        return
    else:
        vc: wavelink.Player = ctx.voice_client
        if not vc.is_playing():
            return await ctx.send(embed=nextcord.Embed(description='*not playing* anything!', color=ctx.author.color))

    #vcloop conditions
        if vc.loop:
            loopstr = 'enabled'
        else:
            loopstr = 'disabled'

        if vc.is_paused():
            state = 'paused'
        else:
            state = 'playing'
        
        for song_req in user_list:
            for song_key in song_req.keys():
                if song_key == vc.track.title:
                    requester = (song_req.get(vc.track.title))

        nowplaying_description = f'[`{vc.track.title}`]({str(vc.track.uri)})\n\n**Requested by**: *{requester}*'
        em = nextcord.Embed(title='now playing\n', description=nowplaying_description, color=ctx.author.color)
        em.add_field(name='**song info**', value=f'• **author**: *{vc.track.author}*\n• **duration**: *{str(datetime.timedelta(seconds=vc.track.length))}*')
        em.add_field(name='**player info**', value=f'• **player volume**: *{vc._volume}*\n• **loop**: *{loopstr}*\n• **current state**: *{state}*', inline=False)

        return await ctx.send(embed=em)
    
@commands.cooldown(1, 2, commands.BucketType.user)  
@bot.command(name='loop', help='loops the current song')
async def loop_command(ctx: commands.Context):
    if await user_connectivity(ctx) == False:
        return
    else:
        vc: wavelink.Player = ctx.voice_client

        try:
            vc.loop ^= True
        except Exception:
            setattr(vc, 'loop', False)
        
        if vc.loop:
            return await ctx.send(embed= nextcord.Embed(title='Loop', description='*enabled*', color=ctx.author.color))
        else:
            return await ctx.send(embed=nextcord.Embed(title='Loop', description='*disabled*', color=ctx.author.color))

@commands.cooldown(1, 2, commands.BucketType.user)  
@bot.command(name='queue', aliases=['q', 'track'], help='displays the current queue')
async def queue_command(ctx: commands.Context):
    if await user_connectivity(ctx) == False:
        return
    else:
        vc: wavelink.Player = ctx.voice_client

        if vc.queue.is_empty:
            return await ctx.send(embed= nextcord.Embed(title='queue', description='*empty*', color=ctx.author.color))
        
        lqstr = '*disabled*' if vc.lq == False else '*enabled*'
        
        em = nextcord.Embed(title='queue', description=f'**loop queue**: *{lqstr}*',color=ctx.author.color)
        global song_count, song, song_queue
        song_queue = vc.queue.copy()
        song_count = 0
        for song in song_queue:
            song_count += 1
            title = song.info['title']
            em.add_field(name=f'‎', value=f'**{song_count}**•{title}',inline=False)
    
        return await ctx.send(embed=em)

@commands.cooldown(1, 2, commands.BucketType.user)  
@bot.command(name="shuffle", aliases=['mix'], help='shuffles the existing queue randomly')
async def shuffle_command(ctx: commands.Context):
    if await user_connectivity(ctx) == False:
        return
    else:
        vc: wavelink.Player = ctx.voice_client
        if song_count > 2:
            random.shuffle(vc.queue._queue)
            return await ctx.send(embed=nextcord.Embed(description=f'*shuffled* the queue', color=ctx.author.color))
        elif vc.queue.is_empty:
            return await ctx.send(embed=nextcord.Embed(description=f'queue is *empty*', color=ctx.author.color))
        else:
            return await ctx.send(embed=nextcord.Embed(description=f'queue must have at least more than *2 songs*', color=ctx.author.color))

@commands.cooldown(1, 2, commands.BucketType.user)  
@bot.command(name='del', aliases=['remove', 'drop'], help='deletes the specified track')
async def del_command(ctx: commands.Context, position: int):
    if await user_connectivity(ctx) == False:
        return
    else:
        vc: wavelink.Player = ctx.voice_client
        if not vc.queue.is_empty:
            if position <= 0:
                return await ctx.send(embed=nextcord.Embed(description=f'position can not be *zero* or *lesser*', color=ctx.author.color))
            elif position > song_count:
                return await ctx.send(embed=nextcord.Embed(description=f'Position **{position}** is outta range', color=ctx.author.color))
            else:
                SongToBeDeleted = vc.queue._queue[position-1].title
                del vc.queue._queue[position-1]
                return await ctx.send(embed=nextcord.Embed(description=f'*{SongToBeDeleted}* removed from the Queue', color=ctx.author.color))
        else:
            return await ctx.send(embed=nextcord.Embed(description='no *songs* available!', color=ctx.author.color))

@commands.cooldown(1, 2, commands.BucketType.user)  
@bot.command(name='skipto',aliases=['goto'], help='skips to the specified track')
async def skipto_command(ctx: commands.Context, position: int):
    if await user_connectivity(ctx) == False:
        return
    else:
        vc: wavelink.Player = ctx.voice_client
        if not vc.queue.is_empty:
            if position <= 0:
                return await ctx.send(embed=nextcord.Embed(description=f'position can not be *zero* or *lesser*', color=ctx.author.color))
            elif position > song_count:
                return await ctx.send(embed=nextcord.Embed(description=f'position *{position}* is outta range', color=ctx.author.color))
            elif position == vc.queue._queue[position-1]:
                return await ctx.send(embed=nextcord.Embed(description='already in that position!', color=ctx.author.color))
            else:
                vc.queue.put_at_front(vc.queue._queue[position-1])
                del vc.queue._queue[position]    
                return await skip_command(ctx)
        else:
            return await ctx.send(embed=nextcord.Embed(description='no *songs* available!', color=ctx.author.color))

@commands.cooldown(1, 2, commands.BucketType.user)  
@bot.command(name='move', aliases=['set'], help='moves the track to the specified position')
async def move_command(ctx: commands.Context, song_position: int, move_position: int):
    if await user_connectivity(ctx) == False:
        return
    else:
        vc: wavelink.Player = ctx.voice_client
        if not vc.queue.is_empty:
            if song_position <= 0 or move_position <= 0:
                return await ctx.send(embed=nextcord.Embed(description=f'position can not be *zero* or *lesser*', color=ctx.author.color))
            elif song_position > song_count or move_position > song_count:
                position = song_position if song_position > song_count else move_position
                return await ctx.send(embed=nextcord.Embed(description=f'position *{position}* is outta range!', color=ctx.author.color))
            elif song_position == move_position:
                return await ctx.send(embed=nextcord.Embed(description=f'it is already in position *{move_position}*', color=ctx.author.color))
            else:
                move_index = move_position-1 if move_position < song_position else move_position
                song_index = song_position if move_position < song_position else song_position-1
                vc.queue.put_at_index(move_index, vc.queue._queue[song_position-1])
                moved_song = vc.queue._queue[song_index]
                del vc.queue._queue[song_index]
                moved_song_name = moved_song.info['title']
                return await ctx.send(embed=nextcord.Embed(description=f'*{moved_song_name}* moved at **{move_position}**', color=ctx.author.color))
        else:
            return await ctx.send(embed=nextcord.Embed(description='no *songs* available!', color=ctx.author.color))

@commands.cooldown(1, 2, commands.BucketType.user)  
@bot.command(name='volume',aliases=['vol'], help='sets the volume')
async def volume_command(ctx: commands.Context, playervolume: int):
    if await user_connectivity(ctx) == False:
        return
    else:
        vc: wavelink.Player = ctx.voice_client
        if vc.is_connected():
            if playervolume > 100:
                return await ctx.send(embed= nextcord.Embed(title='volume', description='supported upto *100%*', color=ctx.author.color))
            elif playervolume < 0:
                return await ctx.send(embed= nextcord.Embed(title='volume', description='can not be *negative*', color=ctx.author.color))
            else:
                await ctx.send(embed=nextcord.Embed(title='volume', description=f'set to *{playervolume}%*', color=ctx.author.color))
                return await vc.set_volume(playervolume)
        elif not vc.is_connected():
            return await ctx.send(embed=nextcord.Embed(description="player *not connected!*", color=ctx.author.color))

@commands.cooldown(1, 2, commands.BucketType.user)  
@bot.command(name='seek', aliases=[], help='seeks or moves the player to specified track position')
async def seek_command(ctx: commands.Context, seekPosition: int):
    if await user_connectivity(ctx) == False:
        return
    else:
        vc: wavelink.Player = ctx.voice_client
        if not vc.is_playing():
            return await ctx.send(embed=nextcord.Embed(description='player *not playing!*', color=ctx.author.color))
        elif vc.is_playing():
            if 0 <= seekPosition <=  vc.track.length:
                await vc.seek(seekPosition*1000)
                return await ctx.send(embed=nextcord.Embed(description=f'player seeked to *{seekPosition}* seconds', color=ctx.author.color))
            else:
                return await ctx.send(embed=nextcord.Embed(description=f'seek length *{seekPosition}* outta range', color=ctx.author.color))


'''main'''

if __name__ == '__main__':
    bot.run(os.environ['tishmish_token'])


