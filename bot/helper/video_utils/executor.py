from __future__ import annotations

from aiofiles import open as aiopen
from aiofiles.os import path as aiopath, makedirs, listdir
from aioshutil import move
from ast import literal_eval
from asyncio import create_subprocess_exec, sleep, gather, Event
from asyncio.subprocess import PIPE
from natsort import natsorted
from os import path as ospath, walk
from time import time

from bot import config_dict, task_dict, task_dict_lock, queue_dict_lock, non_queued_dl, LOGGER, VID_MODE, FFMPEG_NAME
from bot.helper.ext_utils.bot_utils import sync_to_async, cmd_exec, new_task
from bot.helper.ext_utils.files_utils import get_path_size, clean_target
from bot.helper.ext_utils.links_utils import get_url_name
from bot.helper.ext_utils.media_utils import get_document_type, get_media_info, FFProgress
from bot.helper.ext_utils.task_manager import check_running_tasks
from bot.helper.listeners import tasks_listener as task
from bot.helper.mirror_utils.status_utils.ffmpeg_status import FFMpegStatus
from bot.helper.mirror_utils.status_utils.queue_status import QueueStatus
from bot.helper.telegram_helper.message_utils import sendStatusMessage, update_status_message
from bot.helper.video_utils.extra_selector import ExtraSelect

async def get_metavideo(video_file):
    stdout, stderr, rcode = await cmd_exec([
        'ffprobe', '-hide_banner', '-print_format', 'json',
        '-show_format', '-show_streams', video_file
    ])
    if rcode != 0:
        LOGGER.error(stderr)
        return {}, {}
    metadata = literal_eval(stdout)
    return metadata.get('streams', {}), metadata.get('format', {})

class VidEcxecutor(FFProgress):
    def __init__(self, listener: task.TaskListener, path: str, gid: str, metadata=False):
        self.data = None
        self.event = Event()
        self.listener = listener
        self.path = path
        self.name = ''
        self.outfile = ''
        self.size = 0
        self._metadata = metadata
        self._up_path = path
        self._gid = gid
        self._start_time = time()
        self._files = []
        self._qual = {
            '1080p': '1920',
            '720p': '1280',
            '540p': '960',
            '480p': '854',
            '360p': '640'
        }
        super().__init__()
        self.is_cancel = False

    async def _queue(self, update=False):
        if self._metadata:
            add_to_queue, event = await check_running_tasks(self.listener.mid)
            if add_to_queue:
                LOGGER.info('Added to Queue/Download: %s', self.name)
                async with task_dict_lock:
                    task_dict[self.listener.mid] = QueueStatus(self.listener, self.size, self._gid, 'dl')
                await self.listener.onDownloadStart()
                if update:
                    await sendStatusMessage(self.listener.message)
                await event.wait()
                async with task_dict_lock:
                    if self.listener.mid not in task_dict:
                        self.is_cancel = True
                        return
            async with queue_dict_lock:
                non_queued_dl.add(self.listener.mid)

    async def execute(self):
        self._is_dir = await aiopath.isdir(self.path)
        self.mode, self.name, kwargs = self.listener.vidMode
        if not self._metadata and self.mode in config_dict.get('DISABLE_MULTI_VIDTOOLS', []):
            if path := await self._get_video():
                self.path = path
            else:
                return self._up_path
        if self._metadata:
            if not self.name:
                self.name = get_url_name(self.path)
            if not self.name.upper().endswith(('MP4', 'MKV')):
                self.name += '.mkv'
            try:
                self.size = int(self._metadata[1]['size'])
            except Exception as e:
                LOGGER.error(e)
                await self.listener.onDownloadError('Invalid data, check the link!')
                return

        try:
            if handler := getattr(self, f'_{self.mode}', None):
                return await handler(**kwargs)
            else:
                return await self._vid_convert()
        except Exception as e:
            LOGGER.error(e, exc_info=True)
            await self.listener.onUploadError(f"Failed to execute video tool: {e}")
        return self._up_path

    @new_task
    async def _start_handler(self, *args):
        await sleep(0.5)
        await ExtraSelect(self).get_buttons(*args)

    async def _send_status(self, status='wait'):
        async with task_dict_lock:
            task_dict[self.listener.mid] = FFMpegStatus(self.listener, self, self._gid, status)
        if self._metadata and status == 'wait':
            await sendStatusMessage(self.listener.message)

    async def _get_files(self):
        file_list = []
        if self._metadata:
            file_list.append(self.path)
        elif await aiopath.isfile(self.path):
            if (await get_document_type(self.path))[0]:
                file_list.append(self.path)
        else:
            for dirpath, _, files in await sync_to_async(walk, self.path):
                for file in natsorted(files):
                    file_path = ospath.join(dirpath, file)
                    if (await get_document_type(file_path))[0]:
                        file_list.append(file_path)
        return file_list

    async def _get_video(self):
        if not self._is_dir and (await get_document_type(self.path))[0]:
            return self.path
        for dirpath, _, files in await sync_to_async(walk, self.path):
            for file in natsorted(files):
                file_path = ospath.join(dirpath, file)
                if (await get_document_type(file_path))[0]:
                    return file_path

    async def _final_path(self, outfile=''):
        if self._metadata:
            self._up_path = outfile or self.outfile
        else:
            scan_dir = self._up_path if self._is_dir else ospath.dirname(self._up_path)
            if self.listener.seed and not self._is_dir:
                return self._up_path
            
            all_files = []
            for dirpath, _, files in await sync_to_async(walk, scan_dir):
                for file in files:
                    if not file.endswith(('.aria2', '.!qB')):
                        all_files.append(ospath.join(dirpath, file))
            
            if len(all_files) == 1:
                self._up_path = all_files[0]
        return self._up_path

    async def _name_base_dir(self, path, info: str=None, multi: bool=False):
        base_dir, file_name = ospath.split(path)
        if not self.name or multi:
            if info:
                if await aiopath.isfile(path):
                    file_name = ospath.splitext(file_name)[0]
                file_name += f'_{info}.mkv'
            self.name = file_name
        if not self.name.upper().endswith(('MP4', 'MKV')):
            self.name += '.mkv'
        return base_dir if await aiopath.isfile(path) else path

    async def _run_cmd(self, cmd, status='prog'):
        await self._send_status(status)
        try:
            process = await create_subprocess_exec(*cmd, stderr=PIPE)
            _, stderr_bytes = await process.communicate()
            stderr = stderr_bytes.decode().strip() if stderr_bytes else ''

            if process.returncode == 0:
                if not self.listener.seed:
                    await gather(*[clean_target(file) for file in self._files])
                self._files.clear()
                return True
            
            if process.returncode == -9:
                self.is_cancel = True
            else:
                LOGGER.error(f'{stderr}. Failed to {VID_MODE.get(self.mode, "process")}: {self.outfile}')
            self._files.clear()
            return False
        except Exception as e:
            LOGGER.error(f"Error running command: {e}")
            self.is_cancel = True
            return False

    async def _vid_convert(self, **kwargs):
        file_list = await self._get_files()
        if not file_list:
            await self.listener.onUploadError('No video files found for conversion.')
            return self._up_path

        main_video = file_list[0]
        if self._metadata:
            streams = self._metadata[0]
        else:
            self.size = await get_path_size(main_video)
            streams, _ = await get_metavideo(main_video)

        self._start_handler(streams)
        await self.event.wait()
        
        if self.is_cancel or not self.data:
            if not self.is_cancel:
                await self.listener.onUploadError('Conversion cancelled or no options selected.')
            return self._up_path

        await self._queue()
        
        video_codec = self.data.get('video_codec', 'libx264')
        preset = self.data.get('preset', 'medium')
        crf = self.data.get('crf', 23)
        audio_codec = self.data.get('audio_codec', 'aac')
        audio_bitrate = self.data.get('bitrate', '160k')
        audio_channels = self.data.get('audio_channels', 2)
        resolution = self.data.get('resolution')

        if not resolution:
            await self.listener.onUploadError('No resolution selected!')
            return self._up_path
        
        scale_width = self._qual.get(resolution, '1280')

        for file in file_list:
            self.path = file
            base_name, ext = ospath.splitext(ospath.basename(self.path))
            self.outfile = ospath.join(ospath.dirname(self.path), f'{base_name}_{resolution}{ext}')

            cmd = [
                FFMPEG_NAME, '-hide_banner', '-ignore_unknown', '-y',
                '-i', self.path,
                '-vf', f'scale={scale_width}:-2',
                '-c:v', video_codec,
                '-preset', preset,
                '-crf', str(crf),
                '-c:a', audio_codec,
                '-b:a', audio_bitrate,
                '-ac', str(audio_channels),
                self.outfile
            ]

            if not await self._run_cmd(cmd):
                if self.is_cancel: break
                await self.listener.onUploadError("Failed to convert video.")
                return self._up_path

        return await self._final_path()

    async def _rmstream(self, **kwargs):
        file_list = await self._get_files()
        if not file_list:
            await self.listener.onUploadError('No video files found.')
            return self._up_path

        main_video = file_list[0]
        if self._metadata:
            streams = self._metadata[0]
        else:
            self.size = await get_path_size(main_video)
            streams, _ = await get_metavideo(main_video)
            
        self._start_handler(streams)
        await self.event.wait()

        if self.is_cancel or not self.data:
            return self._up_path
        
        await self._queue()
        
        for file in file_list:
            self.path = file
            base_dir, _ = ospath.split(self.path)
            self.outfile = ospath.join(base_dir, self.name)
            self._files.append(self.path)
            
            cmd = [FFMPEG_NAME, '-hide_banner', '-y', '-i', self.path]
            maps = [f'-map 0:{s["map"]}' for s in self.data.get('stream', {}).values() if s['map'] not in self.data.get('sdata', [])]
            cmd.extend(maps)
            cmd.extend(['-c', 'copy', self.outfile])
            
            if not await self._run_cmd(cmd):
                return self._up_path
                
        return await self._final_path()

    async def _swap_stream(self, **kwargs):
        file_list = await self._get_files()
        if not file_list:
            await self.listener.onUploadError('No video files found.')
            return self._up_path

        main_video = file_list[0]
        if self._metadata:
            streams = self._metadata[0]
        else:
            self.size = await get_path_size(main_video)
            streams, _ = await get_metavideo(main_video)

        self._start_handler(streams)
        await self.event.wait()
        
        if self.is_cancel or not self.data:
            return self._up_path

        await self._queue()
        
        for file in file_list:
            self.path = file
            base_dir, _ = ospath.split(self.path)
            self.outfile = ospath.join(base_dir, self.name)
            self._files.append(self.path)
            
            cmd = [FFMPEG_NAME, '-hide_banner', '-y', '-i', self.path]
            remaps = self.data.get('remaps', {})
            video_maps = ['-map', '0:v']
            audio_maps = [f'-map 0:a:{i}' for i in range(len([s for s in streams if s['codec_type'] == 'audio']))]
            
            for old, new in remaps.items():
                audio_maps[new - 1] = f'-map 0:a:{old}'
            
            cmd.extend(video_maps)
            cmd.extend(audio_maps)
            cmd.extend(['-map', '0:s?', '-c', 'copy', self.outfile])
            
            if not await self._run_cmd(cmd):
                return self._up_path

        return await self._final_path()

    async def _vid_vid(self, **kwargs):
        file_list = []
        for dirpath, _, files in await sync_to_async(walk, self.path):
            if len(files) <= 1:
                await self.listener.onUploadError('Only one video found, cannot merge.')
                return self._up_path
            for file in natsorted(files):
                video_file = ospath.join(dirpath, file)
                is_video, _, _ = await get_document_type(video_file)
                if is_video:
                    self.size += await get_path_size(video_file)
                    file_list.append(f"file '{video_file}'")
                    self._files.append(video_file)
        
        self.outfile = self._up_path
        if len(file_list) > 1:
            await self._name_base_dir(self.path)
            await update_status_message(self.listener.message.chat.id)
            input_file = ospath.join(self.path, 'input.txt')
            async with aiopen(input_file, 'w') as f:
                await f.write('\n'.join(file_list))
            
            self.outfile = ospath.join(self.path, self.name)
            cmd = [FFMPEG_NAME, '-ignore_unknown', '-f', 'concat', '-safe', '0', '-i', input_file, '-map', '0', '-c', 'copy', self.outfile, '-y']
            await self._run_cmd(cmd, 'direct')
            await clean_target(input_file)
            
        return await self._final_path()

    async def _vid_aud(self, **kwargs):
        main_video = None
        for dirpath, _, files in await sync_to_async(walk, self.path):
            for file in natsorted(files):
                file_path = ospath.join(dirpath, file)
                is_video, is_audio, _ = await get_document_type(file_path)
                if is_video and not main_video:
                    main_video = file_path
                elif is_audio:
                    self.size += await get_path_size(file_path)
                    self._files.append(file_path)
        
        if not main_video or not self._files:
            await self.listener.onUploadError('Could not find one video and at least one audio file to merge.')
            return self._up_path
            
        self._files.insert(0, main_video)
        self.outfile = self._up_path
        _, size = await gather(self._name_base_dir(self.path), get_path_size(main_video))
        self.size += size
        
        await update_status_message(self.listener.message.chat.id)
        cmd = [FFMPEG_NAME, '-hide_banner', '-ignore_unknown', '-y']
        for i in self._files:
            cmd.extend(['-i', i])
        
        cmd.extend(['-map', '0:v?'])
        for j in range(len(self._files)):
            cmd.extend([f'-map', f'{j}:a?'])
        
        self.outfile = ospath.join(self.path, self.name)
        cmd.extend(['-c', 'copy', self.outfile])
        await self._run_cmd(cmd, 'direct')
        
        return await self._final_path()

    async def _vid_sub(self, **kwargs):
        main_video = None
        for dirpath, _, files in await sync_to_async(walk, self.path):
            for file in natsorted(files):
                file_path = ospath.join(dirpath, file)
                is_video, _, is_sub = await get_document_type(file_path)
                if is_video and not main_video:
                    main_video = file_path
                elif is_sub:
                    self.size += await get_path_size(file_path)
                    self._files.append(file_path)
        
        if not main_video or not self._files:
            await self.listener.onUploadError('Could not find one video and at least one subtitle file to merge.')
            return self._up_path
        
        self._files.insert(0, main_video)
        self.outfile = self._up_path
        _, size = await gather(self._name_base_dir(self.path), get_path_size(main_video))
        self.size += size

        cmd = [FFMPEG_NAME, '-hide_banner', '-ignore_unknown', '-y']
        for i in self._files:
            cmd.extend(['-i', i])
            
        cmd.extend(['-map', '0:v?', '-map', '0:a?'])
        for j in range(1, len(self._files)):
            cmd.extend([f'-map', f'{j}:s?'])
        
        self.outfile = ospath.join(self.path, self.name)
        cmd.extend(['-c', 'copy', self.outfile])
        await self._run_cmd(cmd, 'direct')
        
        return await self._final_path()

    async def _compress(self, **kwargs):
        file_list = await self._get_files()
        if not file_list:
            await self.listener.onUploadError('No video files found for compression.')
            return self._up_path
        
        main_video = file_list[0]
        if self._metadata:
            streams = self._metadata[0]
        else:
            self.size = await get_path_size(main_video)
            streams, _ = await get_metavideo(main_video)
            
        self._start_handler(streams)
        await self.event.wait()
        
        if self.is_cancel or not self.data:
            return self._up_path
            
        await self._queue()
        
        for file in file_list:
            self.path = file
            base_dir, _ = ospath.split(self.path)
            self.outfile = ospath.join(base_dir, self.name)
            self._files.append(self.path)
            
            cmd = [FFMPEG_NAME, '-hide_banner', '-y', '-i', self.path,
                   '-preset', 'slow', '-c:v', 'libx265',
                   '-x265-params', 'log-level=error', '-pix_fmt', 'yuv420p10le',
                   '-crf', '28', '-c:a', 'copy', '-c:s', 'copy', self.outfile]

            if not await self._run_cmd(cmd):
                return self._up_path
                
        return await self._final_path()
