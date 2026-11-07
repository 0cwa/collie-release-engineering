"""Loopback-only public staging origin. Never serve a private signing workspace."""
import argparse
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import urllib.parse
import binary_repo as b

def handler(repo):
    root=repo.resolve()
    class Handler(SimpleHTTPRequestHandler):
        response_status=200
        def translate_path(self,path):
            path=urllib.parse.urlsplit(path).path
            prefix='/fdroid/repo/'
            if not path.startswith(prefix):return str(root/'.never-served')
            parts=Path(urllib.parse.unquote(path[len(prefix):])).parts
            if any(p in {'.','..'} for p in parts):return str(root/'.never-served')
            if not b.public_path('/'.join(parts)):return str(root/'.never-served')
            candidate=root.joinpath(*parts)
            if root not in candidate.resolve().parents:return str(root/'.never-served')
            return str(candidate)
        def send_head(self):
            if not urllib.parse.urlsplit(self.path).path.endswith('.apk'):
                # Mutable entry/index URLs must recover even when replaced within
                # one filesystem timestamp second. Return fresh bytes, not IMS304.
                for name in ('If-Modified-Since','If-None-Match'):
                    if name in self.headers:del self.headers[name]
            return super().send_head()
        def send_response(self,code,message=None):
            self.response_status=code
            super().send_response(code,message)
        def end_headers(self):
            if self.response_status>=400:cache='no-store,max-age=0'
            elif urllib.parse.urlsplit(self.path).path.endswith('.apk'):cache='public,max-age=31536000,immutable'
            else:cache='no-cache,max-age=0,must-revalidate'
            self.send_header('Cache-Control',cache)
            super().end_headers()
        def list_directory(self,path):
            self.send_error(404);return None
    return Handler

def main():
    p=argparse.ArgumentParser();p.add_argument('repo',type=Path);p.add_argument('--fingerprint',required=True);p.add_argument('--approved-generation',required=True);p.add_argument('--approval',action='append',required=True,type=Path);p.add_argument('--port',type=int,default=8765);a=p.parse_args()
    b.approved_generation(a.repo,a.fingerprint,a.approved_generation,a.approval)
    ThreadingHTTPServer(('127.0.0.1',a.port),handler(a.repo)).serve_forever()
if __name__=='__main__':main()
