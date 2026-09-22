# Free YouTube Clipper

This is a no-paid-API adaptation of the YT-Shorts-Automator idea for GitHub Actions.

## What it does
1. Reads up to 10 YouTube video URLs from `sources.txt`
2. Downloads each public source with yt-dlp
3. Transcribes with faster-whisper on CPU
4. Scores 28–60 second candidate moments
5. Selects the best 25 overall
6. Renders vertical 720x1280 MP4 Shorts with a blurred background
7. Uploads the 25 MP4s + CSV/JSON manifest as a GitHub Actions artifact

## Rights requirement
Only use source videos you own, are licensed to reuse, or that are clearly public-domain / Creative Commons with reuse rights. Set:

`# RIGHTS_CONFIRMED=yes`

in `sources.txt` before adding URLs.

## Cost
No paid AI API is used. The workflow runs on GitHub Actions and uses open-source tools.

## Trigger
Updating `clipper_free/sources.txt` starts the workflow automatically.
