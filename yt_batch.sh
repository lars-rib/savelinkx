#!/bin/bash
cd /opt/savelinkx
for client in ios android mweb web_embedded; do
  echo "=== $client ==="
  venv/bin/yt-dlp --cookies cookies/youtube.txt --extractor-args "youtube:player_client=$client" --remote-components ejs:github --no-download --print title "https://www.youtube.com/watch?v=jNQXAC9IVRw" 2>&1 | tail -1
done
