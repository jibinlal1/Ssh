from __future__ import annotations
from ast import literal_eval
from asyncio import Event, wait_for, wrap_future
from functools import partial
from pyrogram.filters import regex, user
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import CallbackQuery
from time import time

from bot import VID_MODE
from bot.helper.ext_utils.bot_utils import new_thread
from bot.helper.ext_utils.status_utils import get_readable_file_size, get_readable_time
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.message_utils import sendMessage, editMessage, deleteMessage
from bot.helper.video_utils import executor as exc

class ExtraSelect:
    def __init__(self, executor: exc.VidEcxecutor):
        self._listener = executor.listener
        self._time = time()
        self._reply = None
        self.executor = executor
        self.event = Event()
        self.is_cancel = False
        self.extension: list[str] = [None, None, 'mkv']
        self.status = ''
        self.swap_selection = {'selected_stream': None, 'remaps': {}}
        
        if not self.executor.data:
            self.executor.data = {}
        
        self.executor.data.setdefault('preset', 'medium')
        self.executor.data.setdefault('crf', 23)
        self.executor.data.setdefault('audio_channels', 2)
        self.executor.data.setdefault('audio_codec', 'aac')
        self.executor.data.setdefault('bitrate', '160k')
        self.executor.data.setdefault('video_codec', 'libx264')

    @new_thread
    async def _event_handler(self):
        pfunc = partial(cb_extra, obj=self)
        handler = self._listener.client.add_handler(
            CallbackQueryHandler(pfunc, filters=regex('^extra') & user(self._listener.user_id)), group=-1)
        try:
            await wait_for(self.event.wait(), timeout=180)
        except:
            self.event.set()
        finally:
            self._listener.client.remove_handler(*handler)

    async def update_message(self, text: str, buttons):
        if not self._reply:
            self._reply = await sendMessage(text, self._listener.message, buttons)
        else:
            await editMessage(text, self._reply, buttons)

    def _streams_select(self, streams: dict):
        buttons = ButtonMaker()
        ddict = self.executor.data
        mode = self.executor.mode

        if 'stream' not in ddict:
            ddict['stream'] = {}
            ddict['sdata'] = []
            for stream in streams:
                indexmap = stream.get('index')
                codec_type = stream.get('codec_type')
                lang = stream.get('tags', {}).get('language', f'#{indexmap}')
                if codec_type not in ['video', 'audio', 'subtitle']:
                    continue
                ddict['stream'][indexmap] = {
                    'info': f"{codec_type.title()} ~ {lang.upper()}",
                    'map': indexmap,
                    'type': codec_type
                }
        
        text = (f'<b>STREAM SETTINGS ~ {self._listener.tag}</b>\n'
                f'<code>{self.executor.name}</code>\n'
                f'File Size: <b>{get_readable_file_size(self.executor.size)}</b>\n')

        if mode == 'rmstream':
            text += f'<b>SELECT STREAMS TO REMOVE</b>\n'
            if sdata := ddict.get('sdata'):
                text += '\nWill be removed:\n'
                for i, sindex in enumerate(sdata, start=1):
                    text += f"{i}. {ddict['stream'][sindex]['info'].replace('✓ ', '')}\n"
            
            for key, value in ddict['stream'].items():
                if value['type'] != 'video':
                    button_info = f"{'✓ ' if key in ddict['sdata'] else ''}{value['info']}"
                    buttons.button_data(button_info, f'extra rmstream {key}')
            buttons.button_data('Reset', 'extra rmstream reset', 'header')
            buttons.button_data('Reverse', 'extra rmstream reverse', 'header')
            buttons.button_data('Continue', 'extra rmstream continue', 'footer')

        elif mode == 'extract':
            audext, subext, vidext = self.extension
            text += (f'<b>SELECT STREAMS TO EXTRACT</b>\n'
                     f'Video: <b>{vidext.upper()}</b> | Audio: <b>{audext.upper()}</b> | Subtitle: <b>{subext.upper()}</b>\n')
            for key, value in ddict['stream'].items():
                buttons.button_data(value['info'], f'extra extract {key}')
            buttons.button_data('Extract All', 'extra extract all', 'footer')

        buttons.button_data('Cancel', 'extra cancel', 'footer')
        text += f'\n<i>Timeout: {get_readable_time(180 - (time() - self._time))}</i>'
        return text, buttons.build_menu(2)

    async def rmstream_select(self, streams: dict):
        await self.update_message(*self._streams_select(streams))
        
    async def extract_select(self, streams: dict):
        await self.update_message(*self._streams_select(streams))

    async def swap_stream_select(self, streams: dict):
        if 'streams' not in self.executor.data:
            self.executor.data['streams'] = streams
        
        buttons = ButtonMaker()
        text = (f"<b>STREAM REORDER SETTINGS ~ {self._listener.tag}</b>\n"
                f"<code>{self.executor.name}</code>\n"
                f"File Size: <b>{get_readable_file_size(self.executor.size)}</b>\n\n")
        
        audio_streams = [s for s in streams if s['codec_type'] == 'audio']
        remaps = self.executor.data.get('remaps', {})
        
        text += "<b>Current Audio Order:</b>\n"
        for i, s in enumerate(audio_streams):
            lang = s.get('tags', {}).get('language', f'#{s.get("index")}')
            new_pos = remaps.get(s['index'], i + 1)
            text += f"Audio {s['index']} ({lang.title()}) -> Pos {new_pos}\n"
        
        text += "\n<b>Select An Audio Stream To Reorder:</b>\n"
        for s in audio_streams:
            lang = s.get('tags', {}).get('language', f'#{s.get("index")}')
            button_text = f"✓ Audio ({s['index']}) ({lang.title()})" if s['index'] in remaps else f"Audio ({s['index']}) ({lang.title()})"
            buttons.button_data(button_text, f"extra swap_stream_select {s['index']}")
        
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        buttons.button_data('Continue ✓', 'extra swap_continue', 'footer')
        await self.update_message(text, buttons.build_menu(2))

    async def _select_swap_position(self, selected_stream_index: int):
        buttons = ButtonMaker()
        text = (f"<b>STREAM REORDER SETTINGS ~ {self._listener.tag}</b>\n"
                f"<code>{self.executor.name}</code>\n"
                f"Selected Stream: <b>{selected_stream_index}</b>\n\n"
                f"Select the new position for this stream:")
        
        occupied_positions = list(self.executor.data.get('remaps', {}).values())
        total_streams = len([s for s in self.executor.data['streams'] if s['codec_type'] == 'audio'])
        
        for i in range(1, total_streams + 1):
            if i in occupied_positions and occupied_positions.count(i) > 0:
                continue
            buttons.button_data(str(i), f"extra swap_position {i}")
        
        buttons.button_data('Back', 'extra swap_back', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        await self.update_message(text, buttons.build_menu(5))

    async def convert_select(self, streams: dict):
        buttons = ButtonMaker()
        hvid = '1080p'
        resolution = {'1080p': '1080p', '720p': '720p', '540p': '540p', '480p': '480p', '360p': '360p'}
        
        for stream in streams:
            if stream.get('codec_type') == 'video':
                vid_height = f"{stream.get('height', 0)}p"
                if vid_height in resolution:
                    hvid = vid_height
                break
        
        keys = list(resolution)
        for key in keys[keys.index(hvid):]:
            buttons.button_data(resolution[key], f'extra convert {key}')
        
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        await self.update_message(f'{self._listener.tag}, Select a resolution to convert.\n<code>{self.executor.name}</code>', buttons.build_menu(2))

    async def show_conversion_options(self, resolution: str):
        self.executor.data['resolution'] = resolution
        buttons = ButtonMaker()
        buttons.button_data(f"Video Codec: {self.executor.data['video_codec']}", 'extra set_video_codec')
        buttons.button_data(f"Preset: {self.executor.data['preset']}", 'extra set_preset')
        buttons.button_data(f"CRF: {self.executor.data['crf']}", 'extra set_crf')
        buttons.button_data(f"Audio Codec: {self.executor.data['audio_codec']}", 'extra set_audio_codec')
        buttons.button_data(f"Audio Channels: {self.executor.data['audio_channels']}", 'extra set_audio_channels')
        buttons.button_data(f"Bitrate: {self.executor.data['bitrate']}", 'extra set_bitrate')
        buttons.button_data('Reset', 'extra reset_conversion', 'header')
        buttons.button_data('Continue', 'extra start_conversion', 'footer')
        buttons.button_data('Back', 'extra back_to_res_select', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        text = (f"<b>Conversion Settings for {resolution}</b>\n"
                f"Adjust parameters or Continue with defaults.\n"
                f"<code>{self.executor.name}</code>")
        await self.update_message(text, buttons.build_menu(2))

    async def _select_option(self, title, current_value, options, callback_prefix):
        buttons = ButtonMaker()
        for val in options:
            prefix = "✓ " if str(val) == str(current_value) else ""
            buttons.button_data(f"{prefix}{val}", f"extra {callback_prefix} {val}")
        buttons.button_data('Back', 'extra back_to_conversion_options', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        await self.update_message(f"<b>Select {title}</b>", buttons.build_menu(3))

    async def get_buttons(self, *args):
        future = self._event_handler()
        
        select_method_name = f'{self.executor.mode}_select'
        if not hasattr(self, select_method_name):
            select_method_name = f'{self.executor.mode.replace("_", "")}_select'

        select_method = getattr(self, select_method_name, None)
        
        if callable(select_method):
            await select_method(*args)
        
        await wrap_future(future)
        
        if self._reply:
            await deleteMessage(self._reply)
        
        if self.is_cancel:
            self.executor.data = None
            self.executor.is_cancel = True
        
        self.event.set()

async def cb_extra(_, query: CallbackQuery, obj: ExtraSelect):
    data = query.data.split()
    cmd = data[1]
    await query.answer()

    if cmd == 'cancel':
        obj.is_cancel = True
        obj.event.set()
        return

    if cmd == 'convert':
        resolution = data[2]
        await obj.show_conversion_options(resolution)
    elif cmd == 'back_to_res_select':
        streams = obj.executor._metadata[0] if obj.executor._metadata else []
        await obj.convert_select(streams)
    elif cmd == 'set_video_codec':
        await obj._select_option("Video Codec", obj.executor.data.get('video_codec'), ['libx264', 'libx265'], "video_codec_value")
    elif cmd == 'video_codec_value':
        obj.executor.data['video_codec'] = data[2]
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'set_preset':
        options = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow']
        await obj._select_option("Preset", obj.executor.data.get('preset'), options, "preset_value")
    elif cmd == 'preset_value':
        obj.executor.data['preset'] = data[2]
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'set_crf':
        await obj._select_option("CRF", obj.executor.data.get('crf'), [18, 20, 22, 23, 24, 26, 28], "crf_value")
    elif cmd == 'crf_value':
        obj.executor.data['crf'] = int(data[2])
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'set_audio_codec':
        await obj._select_option("Audio Codec", obj.executor.data.get('audio_codec'), ['aac', 'ac3', 'copy'], "audio_codec_value")
    elif cmd == 'audio_codec_value':
        obj.executor.data['audio_codec'] = data[2]
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'set_audio_channels':
        await obj._select_option("Audio Channels", obj.executor.data.get('audio_channels'), [1, 2, 6], "audio_channels_value")
    elif cmd == 'audio_channels_value':
        obj.executor.data['audio_channels'] = int(data[2])
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'set_bitrate':
        await obj._select_option("Bitrate", obj.executor.data.get('bitrate'), ['128k', '160k', '192k', '256k', '320k'], "bitrate_value")
    elif cmd == 'bitrate_value':
        obj.executor.data['bitrate'] = data[2]
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'back_to_conversion_options':
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'reset_conversion':
        obj.executor.data.update({'preset': 'medium', 'crf': 23, 'audio_channels': 2, 'audio_codec': 'aac', 'bitrate': '160k', 'video_codec': 'libx264'})
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'start_conversion':
        obj.event.set()
    elif cmd == 'swap_stream_select':
        stream_index = int(data[2])
        obj.swap_selection['selected_stream'] = stream_index
        await obj._select_swap_position(stream_index)
    elif cmd == 'swap_position':
        old_stream_index = obj.swap_selection.get('selected_stream')
        new_position = int(data[2])
        remaps = obj.executor.data.get('remaps', {})
        inverted_remaps = {v: k for k, v in remaps.items()}
        if new_position in inverted_remaps:
            remaps[inverted_remaps[new_position]] = remaps.get(old_stream_index)
        remaps[old_stream_index] = new_position
        obj.executor.data['remaps'] = remaps
        await obj.swap_stream_select(obj.executor.data['streams'])
    elif cmd == 'swap_back':
        await obj.swap_stream_select(obj.executor.data['streams'])
    elif cmd == 'swap_continue':
        obj.event.set()
    elif cmd == 'rmstream':
        ddict = obj.executor.data
        sub_cmd = data[2]
        if sub_cmd == 'reset':
            ddict['sdata'].clear()
        elif sub_cmd == 'continue':
            if ddict.get('sdata'):
                obj.event.set()
            else:
                await query.answer('Please select at least one stream!', show_alert=True)
            return
        elif sub_cmd == 'reverse':
            all_non_video = {k for k, v in ddict['stream'].items() if v.get('type') != 'video'}
            selected = set(ddict['sdata'])
            ddict['sdata'] = list(all_non_video - selected)
        elif sub_cmd.isdigit():
            mapindex = int(sub_cmd)
            if mapindex in ddict['sdata']:
                ddict['sdata'].remove(mapindex)
            else:
                ddict['sdata'].append(mapindex)
        
        for k, v in ddict['stream'].items():
            info = v['info'].replace('✓ ', '')
            if k in ddict['sdata']:
                v['info'] = f"✓ {info}"
            else:
                v['info'] = info
        
        await obj.update_message(*obj._streams_select(ddict['stream']))
    elif cmd == 'extract':
        sub_cmd = data[2]
        if sub_cmd.isdigit():
            obj.executor.data['sdata'] = [int(sub_cmd)]
            obj.event.set()
        elif sub_cmd == 'all':
            obj.executor.data['sdata'] = list(obj.executor.data['stream'].keys())
            obj.event.set()
