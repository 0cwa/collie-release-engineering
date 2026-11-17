"""Download one explicitly approved public engineering release asset, never latest."""
import argparse,hashlib,json,os,re,urllib.request
from pathlib import Path
from pages import MAX_BYTES
from urllib.parse import urlparse

class NoMetadataRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None

class FixedAssetRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        parsed=urlparse(newurl)
        if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443):
            raise ValueError('asset redirect is not fixed HTTPS')
        if parsed.netloc=='api.github.com':
            if not parsed.path.startswith('/repos/0cwa/collie-release-engineering/releases/assets/'):
                raise ValueError('asset redirect left the fixed engineering API endpoint')
        elif parsed.netloc=='release-assets.githubusercontent.com':
            pass
        elif parsed.netloc=='github.com':
            if not parsed.path.startswith('/0cwa/collie-release-engineering/releases/download/'):
                raise ValueError('asset redirect left the fixed engineering release path')
        else:
            raise ValueError('asset redirect left fixed GitHub hosts')
        safe_headers={k:v for k,v in req.headers.items()
                      if k.lower()!='authorization' or parsed.netloc=='api.github.com'}
        return urllib.request.Request(newurl,headers=safe_headers,origin_req_host=req.origin_req_host,
                                      unverifiable=True)

def download(asset_id,expected,destination,*,kind='pages-input'):
    if kind=='pages-input':
        names={'trailblazer-beta-release-input.tar','trailblazer-beta-release-evidence.tar','collie-pages-input.tar'}
        maximum_bytes=MAX_BYTES
    elif kind=='legacy-inventory':
        names={'collie-pages-legacy-inventory.json'}
        maximum_bytes=8*1024*1024
    else:
        raise ValueError('unsupported approved asset kind')
    if not re.fullmatch('[1-9][0-9]*',asset_id) or not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('asset identity/digest')
    temporary=destination.with_suffix(destination.suffix+'.partial')
    if any(path.exists() or path.is_symlink() for path in (destination,temporary)):raise ValueError('fresh input destination and partial required')
    headers={'Accept':'application/vnd.github+json','User-Agent':'Collie-Pages-preparer'}
    token=os.environ.get('GITHUB_TOKEN')
    if token:headers['Authorization']='Bearer '+token
    request=urllib.request.Request('https://api.github.com/repos/0cwa/collie-release-engineering/releases/assets/'+asset_id,headers=headers)
    metadata_opener=urllib.request.build_opener(NoMetadataRedirect())
    with metadata_opener.open(request,timeout=60) as response:metadata=json.load(response)
    url=metadata['browser_download_url']
    browser=urlparse(url)
    if (type(metadata.get('id')) is not int or metadata['id']!=int(asset_id) or
            type(metadata.get('size')) is not int or metadata['size']<=0 or
            browser.scheme!='https' or browser.netloc!='github.com' or
            not browser.path.startswith('/0cwa/collie-release-engineering/releases/download/') or
            metadata.get('name') not in names):
        raise ValueError('asset repository or fixed archive name differs')
    if metadata['size']>maximum_bytes:raise ValueError('input archive budget')
    h=hashlib.sha256();size=0;created=False
    try:
        # Fetch through the fixed API asset endpoint so an engineering-owned
        # draft asset works with this repository's token. The redirect handler
        # strips authorization on every cross-host redirect and validates targets.
        binary_headers={'Accept':'application/octet-stream','User-Agent':'Collie-Pages-preparer'}
        if token:binary_headers['Authorization']='Bearer '+token
        binary_request=urllib.request.Request(
            'https://api.github.com/repos/0cwa/collie-release-engineering/releases/assets/'+asset_id,
            headers=binary_headers)
        opener=urllib.request.build_opener(FixedAssetRedirect())
        with opener.open(binary_request,timeout=60) as response,open(temporary,'xb') as stream:
            final=urlparse(response.geturl())
            if (final.username or final.password or final.port not in (None,443) or not (
                    final.scheme=='https' and final.netloc=='api.github.com' and
                    final.path.startswith('/repos/0cwa/collie-release-engineering/releases/assets/') or
                    final.scheme=='https' and final.netloc=='release-assets.githubusercontent.com' or
                    final.scheme=='https' and final.netloc=='github.com' and
                    final.path.startswith('/0cwa/collie-release-engineering/releases/download/'))):
                raise ValueError('asset download redirected outside fixed GitHub endpoints')
            created=True
            for block in iter(lambda:response.read(1024*1024),b''):
                size+=len(block)
                if size>maximum_bytes:raise ValueError('input archive budget')
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
    p=argparse.ArgumentParser();p.add_argument('--asset-id',required=True);p.add_argument('--sha256',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--kind',choices=('pages-input','legacy-inventory'),default='pages-input')
    a=p.parse_args();download(a.asset_id,a.sha256,a.output,kind=a.kind)
if __name__=='__main__':main()
