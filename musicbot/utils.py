import os, re, uuid, time, logging
logger=logging.getLogger(__name__)

def video_id_from_url(url):
    if not url: return None
    m=re.search(r"(?:v=|youtu\.be/|/watch\?v=)([0-9A-Za-z_-]{11})", url)
    if m: return m.group(1)
    m2=re.search(r"([0-9A-Za-z_-]{11})", url)
    return m2.group(1) if m2 else None

def format_duration(sec):
    if not sec: return "live"
    m,s=divmod(int(sec),60)
    return f"{m}:{s:02d}"

def _make_transformed_filename(src, suffix, downloads_dir):
    base=os.path.basename(src)
    name,ext=os.path.splitext(base)
    uniq=uuid.uuid4().hex[:8]
    return os.path.join(downloads_dir, f"{name}_{suffix}_{uniq}{ext}")
