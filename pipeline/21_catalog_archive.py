#!/usr/bin/env python3
"""
Каталогизация публичного Яндекс.Диска (без скачивания) через public API.
Рекурсивно обходит папки, собирает типы/размеры файлов -> archive_catalog.json + сводка.
"""
import sys, json, time, collections, urllib.request, urllib.parse, pathlib
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

PUB="https://disk.yandex.ru/d/jKyK_ioYmjusVQ"
API="https://cloud-api.yandex.net/v1/disk/public/resources"
HERE=pathlib.Path(__file__).parent
MAX_CALLS=2000

calls=0
def listing(path, offset=0, limit=200):
    global calls; calls+=1
    q=urllib.parse.urlencode({"public_key":PUB,"path":path,"limit":limit,"offset":offset,
                              "fields":"_embedded.items.name,_embedded.items.type,_embedded.items.size,"
                                       "_embedded.items.media_type,_embedded.items.path,_embedded.total,_embedded.limit,_embedded.offset"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(API+"?"+q,timeout=40) as r: return json.load(r)
        except Exception:
            time.sleep(1)
    return {}

ext_n=collections.Counter(); ext_b=collections.Counter(); media_n=collections.Counter()
top_n=collections.Counter(); top_b=collections.Counter()
files=0; total_b=0; folders=0; capped=False
sample=[]

def walk(path, top):
    global files,total_b,folders,capped
    if calls>=MAX_CALLS: capped=True; return
    folders+=1; offset=0
    while True:
        d=listing(path,offset)
        emb=d.get("_embedded",{}); items=emb.get("items",[])
        if not items: break
        for it in items:
            if it.get("type")=="dir":
                walk(it.get("path","").replace("disk:",""), top)
                if capped: return
            else:
                files_local(it, top)
        total=emb.get("total",0); offset+=emb.get("limit",len(items))
        if offset>=total: break
    return

def files_local(it, top):
    global files,total_b
    name=it.get("name",""); size=it.get("size",0) or 0
    ext=("."+name.rsplit(".",1)[1].lower()) if "." in name else "(no ext)"
    ext_n[ext]+=1; ext_b[ext]+=size; media_n[it.get("media_type","?")]+=1
    top_n[top]+=1; top_b[top]+=size
    files+=1; total_b+=size
    if len(sample)<40: sample.append({"path":it.get("path"),"size":size,"media":it.get("media_type")})

root=listing("/")
print("корень:", root.get("name"))
for it in root.get("_embedded",{}).get("items",[]):
    top = it.get("name","")
    if it.get("type")=="dir": walk(it.get("path","").replace("disk:",""), top)
    else: files_local(it, "(root)")
    if capped: print("!!! достигнут лимит вызовов API — каталог неполный"); break

out={"total_files":files,"total_gb":round(total_b/1e9,2),"folders":folders,"api_calls":calls,
     "by_ext":dict(ext_n.most_common()),"gb_by_ext":{k:round(v/1e9,2) for k,v in ext_b.most_common()},
     "by_media":dict(media_n.most_common()),
     "top_folders":{k:{"files":top_n[k],"gb":round(top_b[k]/1e9,2)} for k in top_n}}
(HERE/"archive_catalog.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(f"\nФАЙЛОВ: {files} | ОБЪЁМ: {total_b/1e9:.1f} ГБ | папок: {folders} | API-вызовов: {calls}")
print("ПО РАСШИРЕНИЯМ:", dict(ext_n.most_common(15)))
print("ГБ ПО РАСШИРЕНИЯМ:", {k:round(v/1e9,2) for k,v in ext_b.most_common(10)})
print("-> archive_catalog.json")
