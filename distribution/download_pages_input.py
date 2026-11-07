"""Download one explicitly approved public engineering release asset, never latest."""
import argparse,hashlib,json,os,re,urllib.request
from pathlib import Path
from pages import MAX_BYTES

def download(asset_id,expected,destination):
    if not re.fullmatch('[1-9][0-9]*',asset_id) or not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('asset identity/digest')
    temporary=destination.with_suffix(destination.suffix+'.partial')
    if any(path.exists() or path.is_symlink() for path in (destination,temporary)):raise ValueError('fresh input destination and partial required')
    headers={'Accept':'application/vnd.github+json','User-Agent':'Collie-Pages-preparer'}
    token=os.environ.get('GITHUB_TOKEN')
    if token:headers['Authorization']='Bearer '+token
    request=urllib.request.Request('https://api.github.com/repos/0cwa/collie-release-engineering/releases/assets/'+asset_id,headers=headers)
    with urllib.request.urlopen(request,timeout=60) as response:metadata=json.load(response)
    url=metadata['browser_download_url']
    if metadata['id']!=int(asset_id) or not url.startswith('https://github.com/0cwa/collie-release-engineering/releases/download/'):raise ValueError('asset repository differs')
    if metadata['size']>2*MAX_BYTES:raise ValueError('input archive budget')
    # No token is forwarded to public release-asset redirects.
    h=hashlib.sha256();size=0;created=False
    try:
        with urllib.request.urlopen(url,timeout=60) as response,open(temporary,'xb') as stream:
            created=True
            for block in iter(lambda:response.read(1024*1024),b''):
                size+=len(block)
                if size>2*MAX_BYTES:raise ValueError('input archive budget')
                h.update(block);stream.write(block)
        if size!=metadata['size'] or h.hexdigest()!=expected:raise ValueError('approved input archive differs')
        # Exclusive atomic publication: a concurrently created destination or
        # dangling link is never overwritten. Unlinking our temporary leaves1link.
        os.link(temporary,destination)
        temporary.unlink()
        created=False
    except Exception:
        if created:temporary.unlink(missing_ok=True)
        raise

def main():
    p=argparse.ArgumentParser();p.add_argument('--asset-id',required=True);p.add_argument('--sha256',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();download(a.asset_id,a.sha256,a.output)
if __name__=='__main__':main()
