# 🎙 PodcastSync
### Automatic podcast syncer for Rockbox iPod Classic

---

## What it does

PodcastSync watches for your iPod to be plugged in and automatically downloads
your favourite podcasts to it — organized exactly the way Rockbox expects.

- ✅ Auto-syncs when you plug in your iPod
- ✅ Downloads only new episodes
- ✅ Lets you choose how many back-episodes to keep
- ✅ Removes episodes you've already listened to (Rockbox tracks this)
- ✅ Works on Windows, macOS, and Linux
- ✅ MP3 files organized into `/Podcasts/Show Name/` on your iPod

---

## Requirements

You only need **Python** installed. That's it — no other software needed.

- Download Python from: https://python.org/downloads
- **Windows users**: During install, check ✅ "Add Python to PATH"

---

## How to start

**Windows:**
Double-click `START_WINDOWS.bat`

**macOS / Linux:**
1. Open Terminal
2. Type: `bash ` (with a space at the end)
3. Drag `START_MAC_LINUX.sh` into the Terminal window
4. Press Enter

Your browser will open automatically at **http://localhost:5000**

---

## First-time setup

1. **Add your iPod path** — click "Auto-Detect" with your iPod plugged in, or type the path manually:
   - Windows: `E:\` (or whatever drive letter your iPod gets)
   - macOS: `/Volumes/IPOD`
   - Linux: `/media/yourname/IPOD`

2. **Add podcasts** — paste the RSS feed URL for each show. You can find RSS URLs by searching `[podcast name] RSS feed`.

3. **Plug in your iPod** — PodcastSync will detect it and start syncing automatically!

---

## Tips

- Keep PodcastSync running in the background while you use your computer.
  Every 30 seconds it checks if your iPod is connected.

- The app saves everything in `config.json` and `sync_state.json` next to `app.py`.
  Don't delete these or you'll lose your settings.

- Rockbox marks episodes as "listened" by removing their resume bookmark.
  Make sure you're not using bookmarks for episodes you want to keep.

- To find RSS feeds: most podcast apps show the RSS URL in the show's settings.
  You can also use https://podcastindex.org to search.

---

## File layout on your iPod

```
/Podcasts/
  Darknet Diaries/
    Episode 150 - Shamoon.mp3
    Episode 149 - Mariposa.mp3
  My Favourite Murder/
    Episode 380.mp3
```

---

## Stopping PodcastSync

Close the terminal/command prompt window, or press **Ctrl+C** inside it.

---

## Troubleshooting

**"iPod not found" even when plugged in**
→ Make sure Rockbox is installed (there should be a `.rockbox` folder on your iPod)
→ Try the "Auto-Detect" button
→ On Linux, check that your user has permission to read the mounted drive

**Episodes not downloading**
→ Check the Activity Log in the app for error messages
→ Make sure the RSS feed URL is correct and starts with `http://` or `https://`

**Python not found on macOS**
→ Install via Homebrew: `brew install python3`
→ Or download from https://python.org
