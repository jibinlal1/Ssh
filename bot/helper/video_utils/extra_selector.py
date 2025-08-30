from __future__ import annotations
from ast import literal_eval
from asyncio import Event, wait_for, wrap_future, gather
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
        # Initialize conversion option defaults here to keep consistent state
        if not self.executor.data:
            self.executor.data = {}
        # Default conversion options if not set
        self.executor.data.setdefault('preset', 'medium')
        self.executor.data.setdefault('crf', 23)
        self.executor.data.setdefault('audio_channels', 2)
        self.executor.data.setdefault('audio_codec', 'aac')
        self.executor.data.setdefault('bitrate', '160k')
        self.executor.data.setdefault('video_codec', 'libx264')
        self.executor.data.setdefault('resolution', None)
        self.executor.data.setdefault('audio', None) # For default audio stream

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

    def _streams_select(self, streams: dict = None):
        buttons = ButtonMaker()
        if not self.executor.data:
            self.executor.data = {}
        if 'stream' not in self.executor.data:
            self.executor.data['stream'] = {}
            self.executor.data['sdata'] = []
            for stream in streams:
                indexmap = stream.get('index')
                codec_name = stream.get('codec_name')
                codec_type = stream.get('codec_type')
                lang = stream.get('tags', {}).get('language', f'#{indexmap}')
                if codec_type not in ['video', 'audio', 'subtitle']:
                    continue
                self.executor.data['stream'][indexmap] = {
                    'info': f'{codec_type.title()} ~ {lang.upper()}',
                    'map': indexmap
                }
        mode, ddict = self.executor.mode, self.executor.data
        text = ''
        if mode == 'rmstream':
            text = (f'<b>STREAM REMOVE SETTINGS ~ {self._listener.tag}</b>\n'
                    f'<code>{self.executor.name}</code>\n'
                    f'File Size: <b>{get_readable_file_size(self.executor.size)}</b>\n')
            if sdata := ddict.get('sdata'):
                text += '\nStream will be removed:\n'
                for i, sindex in enumerate(sdata, start=1):
                    text += f"{i}. {ddict['stream'][sindex]['info']}\n".replace('✓ ', '')
            text += '\nSelect available stream below!'
            for key, value in ddict['stream'].items():
                 if value.get('type') != 'video':
                    buttons.button_data(value['info'], f'extra rmstream {key}')
            buttons.button_data('Reset', 'extra rmstream reset', 'header')
            buttons.button_data('Reverse', 'extra rmstream reverse', 'header')
            buttons.button_data('Continue', 'extra rmstream continue', 'footer')
        elif mode == 'extract':
            audext, subext, vidext = self.extension
            text = (f'<b>STREAM EXTRACT SETTINGS ~ {self._listener.tag}</b>\n'
                    f'<code>{self.executor.name}</code>\n'
                    f"File Size: <b>{get_readable_file_size(self.executor.size)}</b>\n"
                    f'Video Format: <b>{vidext.upper()}</b> | Audio Format: <b>{audext.upper()}</b> | Subtitle Format: <b>{subext.upper()}</b>\n'
                    f"Alternative Mode: <b>{'✓ Enable' if ddict.get('alt_mode') else 'Disable'}</b>\n\n"
                    'Select available stream below to unpack!')
            for key, value in ddict['stream'].items():
                buttons.button_data(value['info'], f'extra extract {key}')
            buttons.button_data('✓ ALT Mode' if ddict.get('alt_mode') else 'ALT Mode',
                                f"extra extract alt {ddict.get('alt_mode', False)}", 'footer')
            for ext in self.extension:
                buttons.button_data(ext.upper(), f'extra extract extension {ext}', 'header')
            buttons.button_data('Extract All', f'extra extract video audio subtitle')

        buttons.button_data('Cancel', 'extra cancel', 'footer')
        text += f'\n\n<i>Time Out: {get_readable_time(180 - (time() - self._time))}</i>'
        return text, buttons.build_menu(2)

    def set_default_audio_stream(self, streams: list[dict]):
        if self.executor.data is None:
            self.executor.data = {}
        first_audio_index = next((s['index'] for s in streams if s['codec_type'] == 'audio'), None)
        self.executor.data['audio'] = first_audio_index

    async def compress_select(self, streams: dict):
        self.executor.data = {}
        self.set_default_audio_stream(streams)
        buttons = ButtonMaker()
        for stream in streams:
            indexmap = stream.get('index')
            codec_type = stream.get('codec_type')
            lang = stream.get('tags', {}).get('language')
            if not lang:
                lang = str(indexmap)
            if codec_type == 'video' and 'video' not in self.executor.data:
                self.executor.data['video'] = indexmap
            if codec_type == 'audio':
                buttons.button_data(f'Audio ~ {lang.upper()}', f'extra compress {indexmap}')
        buttons.button_data('Continue (Default Audio)', 'extra compress 0')
        buttons.button_data('Cancel', 'extra cancel')
        await self.update_message(f'{self._listener.tag}, Select available audio or press <b>Continue</b>.\n'
                                  f'<code>{self.executor.name}</code>', buttons.build_menu(2))

    async def rmstream_select(self, streams: dict):
        await self.update_message(*self._streams_select(streams))

    async def swap_stream_select(self, streams: dict):
        if not self.executor.data: self.executor.data = {}
        self.executor.data.update({'streams': streams, 'remaps': self.swap_selection['remaps']})
        buttons = ButtonMaker()
        text = (f"<b>STREAM REORDER SETTINGS ~ {self._listener.tag}</b>\n"
                f"<code>{self.executor.name}</code>\n"
                f"File Size: <b>{get_readable_file_size(self.executor.size)}</b>\n\n")
        all_streams = [s for s in streams if s['codec_type'] == 'audio']
        reordered_streams = self.executor.data.get('remaps', {})
        text += "<b>Current Audio Order:</b>\n"
        for s in all_streams:
            lang = s.get('tags', {}).get('language', f'#{s.get("index")}')
            new_pos = reordered_streams.get(s['index'], f"({s['index']})")
            text += f"Audio {s['index']} ({lang.title()}) -> Pos {new_pos}\n"
        text += "\n<b>Select An Audio Stream To Reorder:</b>\n"
        for s in all_streams:
            lang = s.get('tags', {}).get('language', f'#{s.get("index")}')
            button_text = f"✓ Audio ({s['index']}) ({lang.title()})" if s['index'] in reordered_streams else f"Audio ({s['index']}) ({lang.title()})"
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
            if i in occupied_positions:
                continue
            buttons.button_data(str(i), f"extra swap_position {i}")
        buttons.button_data('Back', 'extra swap_back', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        await self.update_message(text, buttons.build_menu(5))

    async def convert_select(self, streams: dict):
        buttons = ButtonMaker()
        hvid = '1080p'
        resolution = {'1080p': 'Convert 1080p',
                      '720p': 'Convert 720p',
                      '540p': 'Convert 540p',
                      '480p': 'Convert 480p',
                      '360p': 'Convert 360p'}
        for stream in streams:
            if stream['codec_type'] == 'video':
                vid_height = f"{stream.get('height', 0)}p"
                if vid_height in resolution:
                    hvid = vid_height
                break
        keys = list(resolution)
        for key in keys[keys.index(hvid):]:
            buttons.button_data(resolution[key], f'extra convert {key}')
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        await self.update_message(f'{self._listener.tag}, Select available resolution to convert.\n<code>{self.executor.name}</code>', buttons.build_menu(2))

    async def subsync_select(self, *args):
        buttons = ButtonMaker()
        text = ''
        index = 1
        if not self.status:
            for position, file in self.executor.data['list'].items():
                if file.endswith(('srt', '.ass')):
                    ref_file = self.executor.data['final'].get(position, {}).get('ref', '')
                    text += f'{index}. {file} {"✓ " if ref_file else ""}\n'
                    but_txt = f'✓ {index}' if ref_file else str(index)
                    buttons.button_data(but_txt, f'extra subsync {position}')
                    index += 1
            buttons.button_data('Cancel', 'extra cancel', 'footer')
            if self.executor.data.get('final'):
                buttons.button_data('Continue', 'extra subsync continue', 'footer')
        else:
            file = self.executor.data['list'][self.status]
            text = f'Current: <b>{file}</b>\n'
            if ref := self.executor.data['final'].get(self.status, {}).get('ref'):
                 text += f'References: <b>{ref}</b>\n'
            text += '\nSelect Available References Below!\n'
            self.executor.data['final'][self.status] = {'file': file}
            for position, file_ref in self.executor.data['list'].items():
                if position != self.status and file_ref not in [d.get('ref') for d in self.executor.data['final'].values()]:
                    text += f'{index}. {file_ref}\n'
                    buttons.button_data(str(index), f'extra subsync select {position}')
                    index += 1
        await self.update_message(text, buttons.build_menu(5))

    async def extract_select(self, streams: dict):
        ext = ['aac', 'srt', 'mkv']
        for stream in streams:
            codec_name, codec_type = stream.get('codec_name'), stream.get('codec_type')
            if codec_type == 'audio':
                if codec_name in ['ac3', 'eac3', 'm4a', 'mka', 'wav', 'mp3']:
                    ext[0] = codec_name
            elif codec_type == 'subtitle':
                if codec_name == 'ass':
                    ext[1] = 'ass'
        self.extension = ext
        await self.update_message(*self._streams_select(streams))

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
        if extra_mode := getattr(self, f'{self.executor.mode}_select', None):
            await extra_mode(*args)
        await wrap_future(future)
        if self._reply:
            await deleteMessage(self._reply)
        if self.is_cancel:
            self.executor.data = None
            self.executor.is_cancel = True
            await self._listener.onUploadError(f'{VID_MODE[self.executor.mode]} stopped by user!')
        self.executor.event.set()

async def cb_extra(_, query: CallbackQuery, obj: ExtraSelect):
    data = query.data.split()
    cmd = data[1]
    await query.answer()

    if cmd == 'cancel':
        obj.is_cancel = True
        obj.event.set()
        return

    # Conversion Menu Logic
    if cmd == 'convert':
        resolution = data[2]
        await obj.show_conversion_options(resolution)
    elif cmd == 'back_to_res_select':
        await obj.convert_select(obj.executor._metadata[0] if obj.executor._metadata else [])
    elif cmd == 'set_video_codec':
        options = ['libx264', 'libx265']
        await obj._select_option("Video Codec", obj.executor.data.get('video_codec'), options, "video_codec_value")
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
        options = [18, 20, 22, 23, 24, 26, 28, 30]
        await obj._select_option("CRF", obj.executor.data.get('crf'), options, "crf_value")
    elif cmd == 'crf_value':
        obj.executor.data['crf'] = int(data[2])
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'set_audio_codec':
        options = ['aac', 'ac3', 'copy']
        await obj._select_option("Audio Codec", obj.executor.data.get('audio_codec'), options, "audio_codec_value")
    elif cmd == 'audio_codec_value':
        obj.executor.data['audio_codec'] = data[2]
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'set_audio_channels':
        options = [1, 2, 6]
        await obj._select_option("Audio Channels", obj.executor.data.get('audio_channels'), options, "audio_channels_value")
    elif cmd == 'audio_channels_value':
        obj.executor.data['audio_channels'] = int(data[2])
        await obj.show_conversion_options(obj.executor.data['resolution'])
    elif cmd == 'set_bitrate':
        options = ['128k', '160k', '192k', '256k', '320k']
        await obj._select_option("Bitrate", obj.executor.data.get('bitrate'), options, "bitrate_value")
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
        return

    # Other Modes
    elif cmd == 'swap_stream_select':
        stream_index = int(data[2])
        obj.swap_selection['selected_stream'] = stream_index
        await obj._select_swap_position(stream_index)
    elif cmd == 'swap_position':
        old_stream_index = obj.swap_selection.get('selected_stream')
        new_position = int(data[2])
        remaps = obj.executor.data.get('remaps', {})
        remaps[old_stream_index] = new_position
        obj.executor.data['remaps'] = remaps
        await obj.swap_stream_select(obj.executor.data['streams'])
    elif cmd == 'swap_back':
        await obj.swap_stream_select(obj.executor.data['streams'])
    elif cmd == 'swap_continue':
        obj.executor.data['sdata'] = obj.executor.data['remaps']
        obj.event.set()
    elif cmd == 'subsync':
        if len(data) > 2:
            if data[2].isdigit():
                obj.status = int(data[2])
            elif data[2] == 'select':
                obj.executor.data['final'][obj.status]['ref'] = obj.executor.data['list'][int(data[3])]
                obj.status = ''
            elif data[2] == 'continue':
                obj.event.set()
                return
        await obj.subsync_select()
    elif cmd == 'compress':
        obj.executor.data['audio'] = int(data[2]) if len(data) > 2 and data[2].isdigit() else obj.executor.data.get('default_audio')
        obj.event.set()
    elif cmd == 'rmstream':
        ddict = obj.executor.data
        sub_cmd = data[2]
        if sub_cmd == 'reset':
            ddict['sdata'].clear()
        elif sub_cmd == 'continue':
            if ddict['sdata']: obj.event.set()
            else: await query.answer('Please select at least one stream!', True)
            return
        elif sub_cmd == 'reverse':
            new_sdata = [k for k in ddict['stream'] if k not in ddict['sdata'] and ddict['stream'][k].get('type') != 'video']
            ddict['sdata'] = new_sdata
        elif sub_cmd.isdigit():
            mapindex = int(sub_cmd)
            if mapindex in ddict['sdata']: ddict['sdata'].remove(mapindex)
            else: ddict['sdata'].append(mapindex)
        await obj.update_message(*obj._streams_select(ddict['stream']))
    elif cmd == 'extract':
        if len(data) > 2:
            value = data[2]
            if value == 'extension':
                ext_map = {'ass': (1, 'srt'), 'srt': (1, 'ass'), 'aac': (0, 'ac3'), 'ac3': (0, 'eac3'),
                           'eac3': (0, 'm4a'), 'm4a': (0, 'mka'), 'mka': (0, 'wav'), 'wav': (0, 'aac'),
                           'mp4': (2, 'mkv'), 'mkv': (2, 'mp4')}
                if data[3] in ext_map:
                    index, ext = ext_map[data[3]]
                    obj.extension[index] = ext
            elif value == 'alt':
                obj.executor.data['alt_mode'] = not literal_eval(data[3])
            else:
                obj.executor.data.update({'key': int(value) if value.isdigit() else data[2:], 'extension': obj.extension})
                obj.event.set()
                return
            await obj.update_message(*obj._streams_select(obj.executor.data['stream']))
