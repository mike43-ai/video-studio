# video-studio — skill sản xuất video cho Claude Code

Bộ công cụ làm video chạy offline: giọng đọc tiếng Việt / tiếng Anh, chép lời theo từng từ, cắt theo nhịp nhạc,
phụ đề karaoke, mix nhạc + hiệu ứng âm thanh chuẩn −14 LUFS, render bằng HyperFrames.
Tất cả công cụ miễn phí, mã nguồn mở.

## Cài đặt
```bash
git clone <repo-url> ~/.claude/skills/video-studio
bash ~/.claude/skills/video-studio/scripts/install.sh     # ~4 GB tải về lần đầu
~/.claude/skills/video-studio/scripts/vs doctor            # kiểm tra máy
~/.claude/skills/video-studio/scripts/vs selftest          # chạy thử cả quy trình (~3 phút)
```
Cần: macOS hoặc Linux (Windows dùng WSL2), Python 3.11/3.12, ffmpeg, Node ≥ 18, Chrome.

## Dùng
Mở Claude Code và nói, ví dụ: *"làm video TikTok 30 giây giải thích vì sao ngủ trưa giúp tập trung, giọng Mai Anh"*.
Skill tự chạy: kịch bản → giọng → phụ đề → nhạc → render → file cuối.

Hoặc chạy tay:
```bash
alias vs=~/.claude/skills/video-studio/scripts/vs
vs tts script.txt -o . --voice "Hải Đăng"
vs transcribe voice.wav -o words.json --script script.txt --lines lines.json
vs beats music.mp3 -o beats.json
vs timeline --lines lines.json --words words.json --beats beats.json -o .
vs mix --voice voice.wav --music music.mp3 --cues cues.json --timeline timeline.json -o mix.wav
vs new comp --portrait --timeline timeline.js --mix mix.wav
vs render comp && vs deliver comp/renders/render.mp4 mix.wav -o final.mp4
```
Danh sách giọng: `vs voices` (tiếng Việt) · `vs voices --engine kokoro` (tiếng Anh).

## Cập nhật
`git -C ~/.claude/skills/video-studio pull`

## Giấy phép
MIT. Các công cụ bên thứ ba (VieNeu-TTS, Kokoro, faster-whisper, HyperFrames…) theo giấy phép riêng của họ — xem `references/install-and-sharing.md`.
