from exceptions import ConfigVideoLowViewCount, ConfigVideoMaxLength, YoutubeItemNotFound
import os
import shutil
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


class _YtDlpLogger:
    """Keep yt-dlp output quiet except for hard errors."""

    def debug(self, _msg):
        pass

    def info(self, _msg):
        pass

    def warning(self, _msg):
        pass

    def error(self, _msg):
        pass


class YouTube:
    def __init__(self):
        self._logger = _YtDlpLogger()
        self._print_runtime_hints_once()

    def _base_ydl_opts(self):
        return {
            'quiet': True,
            'no_warnings': True,
            'noprogress': True,
            'noplaylist': True,
            'logger': self._logger,
            'retries': 3,
            'fragment_retries': 3,
            'skip_unavailable_fragments': True,
        }

    def _print_runtime_hints_once(self):
        missing_ffmpeg = shutil.which('ffmpeg') is None
        missing_js_runtime = all(
            shutil.which(runtime) is None
            for runtime in ('node', 'deno', 'bun')
        )

        if missing_js_runtime:
            print(
                '[i] No JavaScript runtime found (node/deno/bun). '
                'yt-dlp can still run, but some YouTube formats may be unavailable.'
            )

        if missing_ffmpeg:
            print(
                '[i] ffmpeg not found. Downloads still work, but format selection may be limited.'
            )

    # TODO: Make videos to search configurable via parameter
    def search(self, search_query, max_length, min_view_count, search_count=10):
        return self.search_candidates(search_query, max_length, min_view_count, search_count)[0]

    def search_candidates(self, search_query, max_length, min_view_count, search_count=10):
        with YoutubeDL(self._base_ydl_opts()) as ydl:
            search_result = ydl.extract_info(f"ytsearch{search_count}:{search_query}", download=False)

        entries = search_result.get('entries', []) if search_result else []
        if len(entries) < 1:
            raise YoutubeItemNotFound('Skipped song -- Could not load from YouTube')

        videos_meta = []
        for entry in entries:
            if not entry:
                continue

            duration_seconds = int(entry.get('duration') or 0)
            view_count = int(entry.get('view_count') or 0)
            video_link = entry.get('webpage_url')

            if video_link:
                videos_meta.append((video_link, duration_seconds, view_count))

        if len(videos_meta) < 1:
            raise YoutubeItemNotFound('Skipped song -- Could not load usable YouTube results')

        valid_videos = [
            video for video in videos_meta
            if video[1] < max_length and video[2] > min_view_count
        ]

        if len(valid_videos) > 0:
            # Favor highest-view candidates while keeping several fallbacks.
            return [video[0] for video in sorted(valid_videos, key=lambda video: video[2], reverse=True)]

        highest_view_video = sorted(videos_meta, key=lambda video: video[2], reverse=True)[0]
        if highest_view_video[1] >= max_length:
            raise ConfigVideoMaxLength(
                f'Length {highest_view_video[1]}s exceeds MAX_LENGTH value of {max_length}s [{highest_view_video[0]}]'
            )

        raise ConfigVideoLowViewCount(
            f'View count {highest_view_video[2]} does not meet MIN_VIEW_COUNT value of {min_view_count} [{highest_view_video[0]}]'
        )
    
    def download(self, url, audio_bitrate):
        selected_bitrate_kbps = max(48, int(audio_bitrate / 1000))

        attempts = [
            {
                'format': 'bestaudio*/bestaudio/best',
                'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
            },
            {
                'format': 'bestaudio/best',
                'extractor_args': {'youtube': {'player_client': ['ios', 'web']}},
            },
            {
                'format': 'best',
                'extractor_args': {'youtube': {'player_client': ['tv', 'web']}},
            },
            {
                'format': 'best',
            },
        ]

        last_error = None
        for attempt in attempts:
            ydl_opts = self._base_ydl_opts()
            ydl_opts.update({
                'format': attempt['format'],
                'outtmpl': os.path.join('temp', '%(id)s.%(ext)s'),
            })

            if 'extractor_args' in attempt:
                ydl_opts['extractor_args'] = attempt['extractor_args']

            try:
                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if info.get('entries'):
                        info = info['entries'][0]

                    download_path = info.get('_filename') or ydl.prepare_filename(info)
                    abr = info.get('abr')
                    final_kbps = int(float(abr)) if abr else selected_bitrate_kbps

                return download_path, final_kbps
            except DownloadError as exc:
                last_error = exc
                continue

        if last_error and 'DRM protected' in str(last_error):
            raise YoutubeItemNotFound('Skipped song -- YouTube result is DRM protected')

        raise last_error if last_error else DownloadError('yt-dlp failed to download this video')