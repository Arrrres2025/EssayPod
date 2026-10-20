# EssayPod Live DVR

该模块把播客 TTS 持续返回的 MP3 字节流同步转成 **HLS Event** 时间轴，同时保留原有
`podcast.mp3.partial` 与最终 `podcast.mp3` 流程。

## 工作方式

1. `LiveDVRRecorder` 启动一个独立 FFmpeg 进程。
2. 每个 TTS 音频字节块同时写入原始 MP3 和 FFmpeg 标准输入。
3. FFmpeg 每约 4 秒生成一个 `segment_XXXXXX.ts`。
4. `stream.m3u8` 使用 Event 播放列表并保留从开头至当前时刻的所有片段。
5. 暂停后恢复生成时追加新片段；重新生成则清理上一轮 DVR 文件。
6. 生成完成后播放列表写入 `#EXT-X-ENDLIST`，自然转为可寻址点播音频。

录制器是 best-effort 辅助模块。FFmpeg 不可用或 HLS 转换失败时，原有 MP3 生成仍会继续。

播客 WebSocket 另有应用层音频看门狗：默认 180 秒未收到首个音频帧，或开始后
连续 180 秒未收到新音频帧，任务会失败并在 UI 中提供重试。可分别通过
`PODCAST_FIRST_AUDIO_TIMEOUT` 和 `PODCAST_AUDIO_IDLE_TIMEOUT` 调整；重试会复用已生成的
`condensed.txt`，Live DVR 会从新的音频会话重新开始。
