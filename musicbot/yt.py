import yt_dlp
from .utils import format_duration, video_id_from_url

def download_audio(q, downloads_dir):
    opts={"format":"bestaudio","outtmpl":f"{downloads_dir}/%(id)s.%(ext)s","quiet":True,
          "postprocessors":[{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"320"}]}
    search = f"ytsearch:{q}" if not q.startswith("http") else q
    with yt_dlp.YoutubeDL(opts) as ydl:
        i=ydl.extract_info(search, download=True)
        if "entries" in i: i=i["entries"][0]
        filename=ydl.prepare_filename(i).rsplit('.',1)[0]+'.mp3'
        return {"file":filename,"title":i.get('title','unknown'),"artist":'','duration':i.get('duration',0),'thumb':i.get('thumbnail'), 'webpage':i.get('webpage_url','')}

def fetch_radio_ids(video_id, max_items, RADIO_BATCH):
    if not video_id: return []
    radio_url=f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    opts={"quiet":True,"extract_flat":True,"skip_download":True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info=ydl.extract_info(radio_url, download=False)
            ids=[]
            for e in info.get('entries',[])[:max_items]:
                vid=e.get('id') or e.get('url')
                if not vid: continue
                if len(vid)==11: ids.append(vid)
                else:
                    maybe=video_id_from_url(vid)
                    if maybe: ids.append(maybe)
            return ids
    except Exception:
        return []
