#!/bin/sh
# Makes the files the box tests upload and feed into file pickers: six
# short, different videos (identical ones would be merged as duplicates),
# a picture, a text file and a broken "video".
#
#   tests/box/make-clips.sh DIR
set -eu

dir=${1:?usage: make-clips.sh DIR}
mkdir -p "$dir"
i=0
for name in Lions Frogs Whales Trains Dinos Bees; do
    i=$((i + 1))
    ffmpeg -loglevel error -y \
        -f lavfi -i "testsrc2=size=320x240:rate=15:duration=$((2 + i))" \
        -f lavfi -i "sine=frequency=$((200 * i)):duration=$((2 + i))" \
        -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$dir/$name.mp4"
done
ffmpeg -loglevel error -y -f lavfi -i "color=c=orange:size=320x180" -frames:v 1 "$dir/thumb.png"
echo "not a video" > "$dir/bogus.txt"
head -c 3000 /dev/urandom > "$dir/broken.mp4"
ls "$dir"
