from __future__ import annotations
from ast import literal_eval
from asyncio import Event, wait_for, wrap_future, gather
from functools import partial
from pyrogram.filters import regex, user
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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

    @new_thread
    async def _event_handler(self):
        pfunc = partial(cb_extra, obj=self)
        handler = self._listener.client.add_handler(CallbackQueryHandler(pfunc, filters=regex('^extra') & user(self._listener.user_id)), group=-1)
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

    def _streams_select(self, streams: dict=None):
        buttons = ButtonMaker()
        if not self.executor.data:
            self.executor.data = {}
            self.executor.data.setdefault('stream', {})
            self.executor.data['sdata'] = []
            for stream in streams:
                indexmap, codec_name, codec_type, lang = stream.get('index'), stream.get('codec_name'), stream.get('codec_type'), stream.get('tags', {}).get('language')
                if not lang:
                    lang = str(indexmap)
                if codec_type not in ['video', 'audio', 'subtitle']:
                    continue
                if codec_type == 'audio':
                    self.executor.data['is_audio'] = True
                elif codec_type == 'subtitle':
                    self.executor.data['is_sub'] = True
                self.executor.data['stream'][indexmap] = {'info': f'{codec_type.title()} ~ {lang.upper()}',
                                                          'name': codec_name,
                                                          'map': indexmap,
                                                          'type': codec_type,
                                                          'lang': lang}
        mode, ddict = self.executor.mode, self.executor.data
        for key, value in ddict['stream'].items():
            if mode == 'extract':
                buttons.button_data(value['info'], f'extra {mode} {key}')
                audext, subext, vidext = self.extension
                text = (f'<b>STREAM EXTRACT SETTINGS ~ {self._listener.tag}</b>\n'
                        f'<code>{self.executor.name}</code>\n'
                        f"<b></b>File Size: <b>{get_readable_file_size(self.executor.size)}</b>\n"
                        f'<b></b>Video Format: <b>{vidext.upper()}</b>\n'
                        f'<b></b>Audio Format: <b>{audext.upper()}</b>\n'
                        f'<b></b>Subtitle Format: <b>{subext.upper()}</b>\n'
                        f"<b></b>Alternative Mode: <b>{'✓ Enable' if ddict.get('alt_mode') else 'Disable'}</b>\n\n"
                        'Select avalilable stream below to unpack!')
            elif mode == 'swap_stream':
                pass # The new swap_stream_select method handles this
            else:
                if value['type'] != 'video':
                    buttons.button_data(value['info'], f'extra {mode} {key}')
                text = (f'<b>STREAM REMOVE SETTINGS ~ {self._listener.tag}</b>\n'
                        f'<code>{self.executor.name}</code>\n'
                        f'File Size: <b>{get_readable_file_size(self.executor.size)}</b>\n')
                if sdata := ddict.get('sdata'):
                    text += '\nStream will removed:\n'
                    for i, sindex in enumerate(sdata, start=1):
                        text += f"{i}. {ddict['stream'][sindex]['info']}\n".replace('✓ ', '')
                text += '\nSelect avalilable stream below!'
        if mode == 'extract':
            buttons.button_data('✓ ALT Mode' if ddict.get('alt_mode') else 'ALT Mode', f"extra {mode} alt {ddict.get('alt_mode', False)}", 'footer')
        if ddict.get('is_sub'):
            buttons.button_data('All Subs', f'extra {mode} subtitle')
        if ddict.get('is_audio'):
            buttons.button_data('All Audio', f'extra {mode} audio')
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        if mode == 'extract':
            for ext in self.extension:
                buttons.button_data(ext.upper(), f'extra {mode} extension {ext}', 'header')
            buttons.button_data('Extract All', f'extra {mode} video audio subtitle')
        else:
            buttons.button_data('Reset', f'extra {mode} reset', 'header')
            buttons.button_data('Reverse', f'extra {mode} reverse', 'header')
            buttons.button_data('Continue', f'extra {mode} continue', 'footer')
        text += f'\n\n<i>Time Out: {get_readable_time(180 - (time()-self._time))}</i>'
        return text, buttons.build_menu(2)

    def set_default_audio_stream(self, streams: list[dict]):
        if self.executor.data is None:
            self.executor.data = {}
        first_audio_index = next((s['index'] for s in streams if s['codec_type'] == 'audio'), None)
        self.executor.data['default_audio'] = first_audio_index

    async def compress_select(self, streams: dict):
        self.executor.data = {}
        self.set_default_audio_stream(streams)
        buttons = ButtonMaker()
        for stream in streams:
            indexmap, codec_type, lang = stream.get('index'), stream.get('codec_type'), stream.get('tags', {}).get('language')
            if not lang:
                lang = str(indexmap)
            if codec_type == 'video' and indexmap == 0:
                self.executor.data['video'] = indexmap
            if codec_type == 'video' and 'video' not in self.executor.data:
                self.executor.data['video'] = indexmap
            if codec_type == 'audio':
                buttons.button_data(f'Audio ~ {lang.upper()}', f'extra compress {indexmap}')
        buttons.button_data('Continue', 'extra compress 0')
        buttons.button_data('Cancel', 'extra cancel')
        await self.update_message(f'{self._listener.tag}, Select available audio or press <b>Continue (no audio)</b>.\n<code>{self.executor.name}</code>', buttons.build_menu(2))

    async def rmstream_select(self, streams: dict):
        self.executor.data = {}
        await self.update_message(*self._streams_select(streams))
    
    async def swap_stream_select(self, streams: dict):
        if not self.executor.data:
            self.executor.data = {}
        self.executor.data.update({'streams': streams, 'remaps': self.swap_selection['remaps']})
        self.set_default_audio_stream(streams)
        buttons = ButtonMaker()
        
        text = (f"<b>STREAM REORDER SETTINGS ~ {self._listener.tag}</b>\n"
                f"<code>{self.executor.name}</code>\n"
                f"File Size: <b>{get_readable_file_size(self.executor.size)}</b>\n\n")

        all_streams = [s for s in streams if s['codec_type'] == 'audio']
        
        reordered_streams = self.executor.data.get('remaps', {})
        
        text += "<b>Current Audio Order:</b>\n"
        for s in all_streams:
            lang = s.get('tags', {}).get('language', f'#{s.get("index")}')
            new_pos = reordered_streams.get(s['index'], s['index'])
            text += f"Audio {s['index']} ({lang.title()}) -> {new_pos}\n"

        text += "\n<b>Select An Audio Stream To Reorder:</b>\n"

        for s in all_streams:
            lang = s.get('tags', {}).get('language', f'#{s.get("index")}')
            button_text = f"✓ Audio ({s['index']}) ({lang.title()})" if s['index'] in reordered_streams else f"Audio ({s['index']}) ({lang.title()})"
            buttons.button_data(button_text, f"extra swap_stream_select {s['index']}")
        
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        buttons.button_data('Continue ✓', 'extra swap_continue', 'footer')
        
        await self.update_message(text, buttons.build_menu(2))

    async def _select_swap_position(self, selected_stream_index: int, total_streams: int):
        buttons = ButtonMaker()
        text = (f"<b>STREAM REORDER SETTINGS ~ {self._listener.tag}</b>\n"
                f"<code>{self.executor.name}</code>\n"
                f"Selected Stream: <b>{selected_stream_index}</b>\n\n"
                f"Select the new position for this stream:")
        
        # Determine occupied positions from remaps
        occupied_positions = list(self.swap_selection['remaps'].values())
        
        # Dynamically create buttons for available positions
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
        resulution = {'1080p': 'Convert 1080p',
                      '720p': 'Convert 720p',
                      '540p': 'Convert 540p',
                      '480p': 'Convert 480p',
                      '360p': 'Convert 360p'}
        for stream in streams:
            if stream['codec_type'] == 'video':
                vid_height = f'{stream["height"]}p'
                if vid_height in resulution:
                    hvid = vid_height
                break
        keys = list(resulution)
        for key in keys[keys.index(hvid)+1:]:
            buttons.button_data(resulution[key], f'extra convert {key}')

        buttons.button_data('Custom FFmpeg', 'extra convert custom_options')
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        await self.update_message(f'{self._listener.tag}, Select available resulution to convert.\n<code>{self.executor.name}</code>', buttons.build_menu(2))

    async def _select_custom_options(self, streams: dict):
        if not self.executor.data:
            self.executor.data = {}
        self.executor.data.update({'streams': streams, 'remaps': self.swap_selection['remaps']})
        self.set_default_audio_stream(streams)
        
        buttons = ButtonMaker()
        text = (f"<b>CUSTOM FFmpeg SETTINGS ~ {self._listener.tag}</b>\n"
                f"<code>{self.executor.name}</code>\n"
                f"File Size: <b>{get_readable_file_size(self.executor.size)}</b>\n\n"
                "Please choose your custom settings:")
        
        buttons.button_data('CRF', 'extra convert crf_mode')
        buttons.button_data('Video Codec', 'extra convert vcodec_mode')
        buttons.button_data('Bitrate', 'extra convert bitrate_mode')
        buttons.button_data('Preset', 'extra convert preset_mode')
        buttons.button_data('Resolution', 'extra convert resolution_mode')
        
        bit_depth_button_text = f"{'✓ ' if self.executor.data.get('bit_depth') == '10bit' else ''}10bit"
        buttons.button_data(bit_depth_button_text, 'extra convert bit_depth_toggle')
        
        buttons.button_data('FPS', 'extra convert fps_mode')

        buttons.button_data('Back', 'extra convert back', 'footer')
        buttons.button_data('Continue ✓', 'extra convert continue_custom', 'footer')
        
        await self.update_message(text, buttons.build_menu(2))
    
    async def _select_crf_quality(self, streams: dict):
        if not self.executor.data:
            self.executor.data = {}
        self.executor.data.update({'streams': streams})
        buttons = ButtonMaker()
        text = (f'<b>CRF CONVERT SETTINGS ~ {self._listener.tag}</b>\n'
                f'<code>{self.executor.name}</code>\n\n'
                'Please select a CRF value:\n'
                '<i>Lower value means higher quality but larger size.</i>')
        
        for crf_value in ['18', '21', '23', '25', '28']:
            buttons.button_data(f'CRF {crf_value}', f'extra convert crf_set {crf_value}')
        
        buttons.button_data('Custom CRF', 'extra convert custom_crf_input')
        buttons.button_data('Back', 'extra convert custom_options', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')

        await self.update_message(text, buttons.build_menu(3))
    
    async def _select_vcodec(self, streams: dict):
        if not self.executor.data:
            self.executor.data = {}
        self.executor.data.update({'streams': streams})
        buttons = ButtonMaker()
        text = (f'<b>VIDEO CODEC SETTINGS ~ {self._listener.tag}</b>\n'
                f'<code>{self.executor.name}</code>\n\n'
                'Please select a video codec:')
        
        vcodecs = ['libx264', 'libx265', 'copy']
        for vcodec in vcodecs:
            buttons.button_data(vcodec, f'extra convert vcodec_set {vcodec}')
        
        buttons.button_data('Back', 'extra convert custom_options', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')

        await self.update_message(text, buttons.build_menu(3))

    async def _select_bitrate(self, streams: dict):
        if not self.executor.data:
            self.executor.data = {}
        self.executor.data.update({'streams': streams})
        buttons = ButtonMaker()
        text = (f'<b>VIDEO BITRATE SETTINGS ~ {self._listener.tag}</b>\n'
                f'<code>{self.executor.name}</code>\n\n'
                'Please select a video bitrate:')

        bitrates = ['1M', '2M', '5M', '10M', '20M']
        for bitrate in bitrates:
            buttons.button_data(bitrate, f'extra convert bitrate_set {bitrate}')

        buttons.button_data('Back', 'extra convert custom_options', 'footer')
        buttons.button_data('Custom Bitrate', 'extra convert custom_bitrate_input')
        buttons.button_data('Cancel', 'extra cancel', 'footer')

        await self.update_message(text, buttons.build_menu(3))

    async def _select_preset(self, streams: dict):
        if not self.executor.data:
            self.executor.data = {}
        self.executor.data.update({'streams': streams})
        buttons = ButtonMaker()
        text = (f'<b>VIDEO PRESET SETTINGS ~ {self._listener.tag}</b>\n'
                f'<code>{self.executor.name}</code>\n\n'
                'Please select a video preset:\n'
                '<i>Slower presets offer better compression.</i>')

        presets = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow']
        for preset in presets:
            buttons.button_data(preset, f'extra convert preset_set {preset}')

        buttons.button_data('Back', 'extra convert custom_options', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')

        await self.update_message(text, buttons.build_menu(3))

    async def _select_resolution(self, streams: dict):
        if not self.executor.data:
            self.executor.data = {}
        self.executor.data.update({'streams': streams})
        buttons = ButtonMaker()
        text = (f'<b>RESOLUTION CONVERT SETTINGS ~ {self._listener.tag}</b>\n'
                f'<code>{self.executor.name}</code>\n\n'
                'Please select a resolution:')

        resolutions = ['1920x1080', '1280x720', '854x480', '640x360', '2560x1440', '3840x2160']
        for res in resolutions:
            buttons.button_data(res, f'extra convert resolution_set {res}')
            
        buttons.button_data('Custom Resolution', 'extra convert custom_resolution_input')
        buttons.button_data('Back', 'extra convert custom_options', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')

        await self.update_message(text, buttons.build_menu(3))

    async def _select_bit_depth(self, streams: dict):
        if not self.executor.data:
            self.executor.data = {}
        self.executor.data.update({'streams': streams})
        buttons = ButtonMaker()
        text = (f'<b>BIT DEPTH CONVERT SETTINGS ~ {self._listener.tag}</b>\n'
                f'<code>{self.executor.name}</code>\n\n'
                'Please select a bit depth:')

        bit_depths = ['8bit', '10bit']
        for depth in bit_depths:
            buttons.button_data(depth, f'extra convert bit_depth_set {depth}')

        buttons.button_data('Back', 'extra convert custom_options', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')

        await self.update_message(text, buttons.build_menu(2))

    async def _select_fps(self, streams: dict):
        if not self.executor.data:
            self.executor.data = {}
        self.executor.data.update({'streams': streams})
        buttons = ButtonMaker()
        text = (f'<b>FPS CONVERT SETTINGS ~ {self._listener.tag}</b>\n'
                f'<code>{self.executor.name}</code>\n\n'
                'Please select a frame rate:')

        fps_options = ['24', '25', '30', '60']
        for fps in fps_options:
            buttons.button_data(f'{fps} FPS', f'extra convert fps_set {fps}')

        buttons.button_data('Custom FPS', 'extra convert custom_fps_input')
        buttons.button_data('Back', 'extra convert custom_options', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')

        await self.update_message(text, buttons.build_menu(3))

    async def _await_text_input(self, prompt, key_to_set, back_callback):
        self.status = 'awaiting_custom_input'
        self.executor.data['custom_key'] = key_to_set
        
        buttons = ButtonMaker()
        buttons.button_data('Back', f'extra convert {back_callback}', 'footer')
        buttons.button_data('Cancel', 'extra cancel', 'footer')
        
        await self.update_message(prompt, buttons.build_menu(1))
        self.event.set()

    async def subsync_select(self, streams: dict):
        buttons = ButtonMaker()
        text = ''
        index = 1
        if not self.status:
            for possition, file in self.executor.data['list'].items():
                if file.endswith(('srt', '.ass')):
                    ref_file = self.executor.data['final'].get(possition, {}).get('ref', '')
                    text += f'{index}. {file} {"✓ " if ref_file else ""}\n'
                    but_txt = f'✓ {index}' if ref_file else index
                    buttons.button_data(but_txt, f'extra subsync {possition}')
                    index += 1
            buttons.button_data('Cancel', 'extra cancel', 'footer')
            if self.executor.data['final']:
                buttons.button_data('Continue', 'extra subsync continue', 'footer')
        else:
            file: dict = self.executor.data['list'][self.status]
            text = (f'Current: <b>{file}</b>\n'
                    f'References: <b>{ref}</b>\n' if (ref := self.executor.data['final'].get(self.status, {}).get('ref')) else ''
                    '\nSelect Available References Below!\n')
            self.executor.data['final'][self.status] = {'file': file}
            for possition, file in self.executor.data['list'].items():
                if possition != self.status and file not in self.executor.data['final'].values():
                    text += f'{index}. {file}\n'
                    buttons.button_data(index, f'extra subsync select {possition}')
                    index += 1
        await self.update_message(text, buttons.build_menu(5))

    async def extract_select(self, streams: dict):
        self.executor.data = {}
        ext = [None, None, 'mkv']
        for stream in streams:
            codec_name, codec_type = stream.get('codec_name'), stream.get('codec_type')
            if codec_type == 'audio' and not ext[0]:
                match codec_name:
                    case 'mp3':
                        ext[0] = 'ac3'
                    case 'aac' | 'ac3' | 'ac3' | 'eac3' | 'm4a' | 'mka' | 'wav' as value:
                        ext[0] = value
                    case _:
                        ext[0] = 'aac'
            elif codec_type == 'subtitle' and not ext[1]:
                ext[1] = 'srt' if codec_name == 'subrip' else 'ass'
        if not ext[0]:
            ext[0] = 'aac'
        if not ext[1]:
            ext[1] = 'srt'
        self.extension = ext
        await self.update_message(*self._streams_select(streams))

    async def get_buttons(self, *args):
        future = self._event_handler()
        if extra_mode := getattr(self, f'{self.executor.mode}_select', None):
            await extra_mode(*args)
        await wrap_future(future)
        self.executor.event.set()
        await deleteMessage(self._reply)
        if self.is_cancel:
            self._listener.suproc = 'cancelled'
            await self._listener.onUploadError(f'{VID_MODE[self.executor.mode]} stopped by user!')


async def cb_extra(_, query: CallbackQuery, obj: ExtraSelect):
    data = query.data.split()
    match data[1]:
        case 'cancel':
            await query.answer()
            obj.is_cancel = obj.executor.is_cancel = True
            obj.executor.data = None
            obj.event.set()
        case 'swap_stream_select':
            await query.answer()
            stream_index = int(data[2])
            obj.swap_selection['selected_stream'] = stream_index
            total_streams = len([s for s in obj.executor.data['streams'] if s['codec_type'] == 'audio'])
            await obj._select_swap_position(stream_index, total_streams)
        case 'swap_position':
            await query.answer()
            old_stream_index = obj.swap_selection.get('selected_stream')
            if not old_stream_index:
                await query.answer("Please select a stream first!", show_alert=True)
                return

            new_position = int(data[2])
            obj.swap_selection['selected_stream'] = None
            
            remaps = obj.executor.data.get('remaps', {})

            if new_position in remaps.values():
                await query.answer(f"Position {new_position} is already taken. Please choose another position.", show_alert=True)
                obj.swap_selection['selected_stream'] = old_stream_index
                total_streams = len([s for s in obj.executor.data['streams'] if s['codec_type'] == 'audio'])
                await obj._select_swap_position(old_stream_index, total_streams)
                return

            remaps[old_stream_index] = new_position
            obj.executor.data['remaps'] = remaps
            
            await obj.swap_stream_select(obj.executor.data['streams'])
        case 'swap_back':
            await query.answer()
            obj.swap_selection = {'selected_stream': None, 'remaps': {}}
            await obj.swap_stream_select(obj.executor.data['streams'])
        case 'swap_continue':
            obj.executor.data['sdata'] = obj.executor.data['remaps']
            await query.answer('Starting the reordering process.', show_alert=True)
            obj.event.set()
        case 'subsync':
            if data[2].isdigit():
                obj.status = int(data[2])
            elif data[2] == 'select':
                obj.executor.data['final'][obj.status]['ref'] = obj.executor.data['list'][int(data[3])]
                obj.status = ''
            elif data[2] == 'continue':
                obj.event.set()
                return
            await gather(query.answer(), obj.subsync_select(None))
        case 'compress':
            await query.answer()
            obj.executor.data['audio'] = int(data[2])
            obj.event.set()
        case 'convert':
            await query.answer()
            if not obj.executor.data:
                obj.executor.data = {}
            if 'streams' not in obj.executor.data:
                obj.executor.data['streams'] = []
            
            match data[2]:
                case 'crf_mode':
                    await obj._select_crf_quality(obj.executor.data['streams'])
                case 'crf_set':
                    obj.executor.data['crf'] = int(data[3])
                    obj.event.set()
                case 'vcodec_mode':
                    await obj._select_vcodec(obj.executor.data['streams'])
                case 'vcodec_set':
                    obj.executor.data['vcodec'] = data[3]
                    obj.event.set()
                case 'bitrate_mode':
                    await obj._select_bitrate(obj.executor.data['streams'])
                case 'bitrate_set':
                    obj.executor.data['bitrate'] = data[3]
                    obj.event.set()
                case 'preset_mode':
                    await obj._select_preset(obj.executor.data['streams'])
                case 'preset_set':
                    obj.executor.data['preset'] = data[3]
                    obj.event.set()
                case 'resolution_mode':
                    await obj._select_resolution(obj.executor.data['streams'])
                case 'resolution_set':
                    obj.executor.data['resolution'] = data[3]
                    obj.event.set()
                case 'bit_depth_toggle':
                    current_value = obj.executor.data.get('bit_depth')
                    if current_value == '10bit':
                        obj.executor.data.pop('bit_depth')
                    else:
                        obj.executor.data['bit_depth'] = '10bit'
                    await obj._select_custom_options(obj.executor.data['streams'])
                case 'fps_mode':
                    await obj._select_fps(obj.executor.data['streams'])
                case 'fps_set':
                    obj.executor.data['fps'] = int(data[3])
                    obj.event.set()
                case 'custom_options':
                    await obj._select_custom_options(obj.executor.data['streams'])
                case 'continue_custom':
                    await query.answer('Starting the custom convert process.', show_alert=True)
                    obj.event.set()
                case 'back':
                    if 'streams' not in obj.executor.data:
                         await obj.convert_select(None)
                    else:
                        await obj.convert_select(obj.executor.data['streams'])
                case 'custom_crf_input':
                    await obj._await_text_input(
                        prompt="Please send a custom CRF value (e.g., `24`).\n<i>Timeout: 60s.</i>",
                        key_to_set='crf',
                        back_callback='crf_mode'
                    )
                case 'custom_bitrate_input':
                    await obj._await_text_input(
                        prompt="Please send a custom bitrate value (e.g., `1M` or `1024K`).\n<i>Timeout: 60s.</i>",
                        key_to_set='bitrate',
                        back_callback='bitrate_mode'
                    )
                case 'custom_resolution_input':
                    await obj._await_text_input(
                        prompt="Please send a custom resolution (e.g., `1920x1080` or `1280:-2`).\n<i>Timeout: 60s.</i>",
                        key_to_set='resolution',
                        back_callback='resolution_mode'
                    )
                case 'custom_fps_input':
                    await obj._await_text_input(
                        prompt="Please send a custom FPS value (e.g., `30`).\n<i>Timeout: 60s.</i>",
                        key_to_set='fps',
                        back_callback='fps_mode'
                    )
                case _:
                    if not obj.executor.data:
                        obj.executor.data = {}
                    obj.executor.data = data[2]
                    obj.event.set()
        case 'rmstream':
            ddict: dict = obj.executor.data
            match data[2]:
                case 'reset':
                    if sdata := ddict['sdata']:
                        await query.answer()
                        for mapindex in sdata:
                            info = ddict['stream'][mapindex]['info']
                            ddict['stream'][mapindex]['info'] = info.replace('✓ ', '')
                        sdata.clear()
                        await obj.update_message(*obj._streams_select())
                    else:
                        await query.answer('No any selected stream to reset!', True)
                case 'continue':
                    if ddict['sdata']:
                        await query.answer()
                        obj.event.set()
                    else:
                        await query.answer('Please select at least one stream!', True)
                case 'audio' | 'subtitle' as value:
                    await query.answer()
                    obj.executor.data['key'] = value
                    obj.event.set()
                case 'reverse':
                    if ddict['sdata']:
                        await query.answer()
                        new_sdata = [x for x in ddict['stream'] if x not in ddict['sdata'] and x != 0]
                        for key, value in ddict['stream'].items():
                            info = value['info']
                            ddict['stream'][key]['info'] = f'✓ {info}' if key in new_sdata else info.replace('✓ ', '')
                        ddict['sdata'] = new_sdata
                        await obj.update_message(*obj._streams_select())
                    else:
                        await query.answer('No any selected stream to revers!', True)
                case value:
                    await query.answer()
                    mapindex = int(value)
                    info = ddict['stream'][mapindex]['info']
                    if mapindex in ddict['sdata']:
                        ddict['sdata'].remove(mapindex)
                        ddict['stream'][mapindex]['info'] = info.replace('✓ ', '')
                    else:
                        ddict['sdata'].append(mapindex)
                        ddict['stream'][mapindex]['info'] = f'✓ {info}'
                    await obj.update_message(*obj._streams_select())
        case 'extract':
            value = data[2]
            await query.answer()
            if value in ('extension', 'alt'):
                ext_dict = {'ass': [1, 'srt'],
                            'srt': [1, 'ass'],
                            'aac': [0, 'ac3'],
                            'ac3': [0, 'eac3'],
                            'eac3': [0, 'm4a'],
                            'm4a': [0, 'mka'],
                            'mka': [0, 'wav'],
                            'wav': [0, 'aac'],
                            'mp4': [2, 'mkv'],
                            'mkv': [2, 'mp4']}
                if data[3] in ext_dict:
                    index, ext = ext_dict[data[3]]
                    obj.extension[index] = ext
                if value == 'alt':
                    obj.executor.data['alt_mode'] = not literal_eval(data[3])
                await obj.update_message(*obj._streams_select())
            else:
                obj.executor.data.update({'key': int(value) if value.isdigit() else data[2:],
                                          'extension': obj.extension})
                obj.event.set()
