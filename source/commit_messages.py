#!/usr/bin/env python3
"""Export editable, complete commit messages before source generation.

The JSON is a versioned publication input; Markdown is its human review view.
No Git objects, refs, commits, or remote state are changed by this command.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat


def subject(text):
    # Export proposes wording; it never truncates the exact wording consumed by
    # publication. Owners can replace any proposed subject before freezing.
    return text.splitlines()[0].strip().rstrip('.')


def normalize_new_text(text):
    text=re.sub(r'(?im)^AI-Generated:\s*true\s*$', '', text)
    text=re.sub(r'(?im)^LLM-Assisted:\s*true\s*$', '', text)
    return text.replace('AI-assisted','LLM-assisted').replace('AI assistance','LLM assistance').strip()


def required_provenance(layer):
    return 'Composite-generated: true\nLLM-Assisted: true\nOriginal-provenance: '+normalize_new_text(layer['provenance'])


def preview(plan,plan_sha256):
    entries=[]
    for index,layer in enumerate(plan['layers']):
        entries.append({'index':index,'changes':layer['name'],'subject':subject(layer['name']),
                        'body':normalize_new_text(layer['name']).rstrip('.')+'.\n\n'+required_provenance(layer)})
    return {'schema':1,'publication_plan_sha256':plan_sha256,'layers':entries,
            'integration':{'subject':'Bring the reviewed Collie source into the application repository',
                           'body':'Keep prior Collie and authentic Signal history while making the complete reviewed application tree visible.\n\nComposite-generated: true\nLLM-Assisted: true'},
            'pr_title':'Publish Collie application source with declared build inputs',
            'merge':{'subject':'Publish Collie application source with declared build inputs',
                     'body':'Preserve both histories and adopt the complete reviewed source tree. Production activation requires its separately approved inputs and acceptance.\n\nLLM-Assisted: true'}}


def message(value):
    if not isinstance(value,dict) or set(value)!={'subject','body'}:raise ValueError('message requires subject and body')
    if not isinstance(value['subject'],str) or not value['subject'].strip() or any(ord(c)<32 for c in value['subject']) or len(value['subject'])>120:raise ValueError('invalid message subject')
    if not isinstance(value['body'],str) or '\x00' in value['body'] or '\r' in value['body'] or len(value['body'])>12000:raise ValueError('invalid message body')
    if 'AI-Generated:' in value['body']:raise ValueError('new message must use the visible LLM-Assisted trailer')
    return value['subject']+'\n\n'+value['body']+'\n'


def validate(data,plan,plan_sha256):
    if set(data)!={'schema','publication_plan_sha256','layers','integration','pr_title','merge'} or type(data['schema']) is not int or data['schema']!=1 or data['publication_plan_sha256']!=plan_sha256:raise ValueError('message input does not bind the exact publication plan')
    if not isinstance(data['layers'],list) or len(data['layers'])!=len(plan['layers']):raise ValueError('message layer count differs')
    for index,(entry,layer) in enumerate(zip(data['layers'],plan['layers'])):
        if set(entry)!={'index','changes','subject','body'} or entry['index']!=index or type(entry['index']) is not int or entry['changes']!=layer['name']:raise ValueError('message layer order/meaning differs')
        message({k:entry[k] for k in ('subject','body')})
        if required_provenance(layer) not in entry['body']:raise ValueError('required visible provenance/authorship removed')
    message(data['integration']);message(data['merge'])
    if any('LLM-Assisted: true' not in value['body'] for value in (data['integration'],data['merge'])):raise ValueError('new integration/merge message requires visible LLM assistance')
    if 'Composite-generated: true' not in data['integration']['body']:raise ValueError('integration composite provenance missing')
    message({'subject':data['pr_title'],'body':''})
    return data


def markdown(data):
    rows=['# Application commit wording for review','',
          'Draft wording only. Edit the JSON subjects/bodies in one batch or delegate a style. No final source commits have been generated. Required provenance appears below exactly as the publisher will consume it.','',
          'Publication plan SHA256: `'+data['publication_plan_sha256']+'`','']
    for entry in data['layers']:
        rows+=['## '+str(entry['index']+1)+'. '+entry['subject'],'','Changes: '+entry['changes'].splitlines()[0],'','```text',message({k:entry[k] for k in ('subject','body')}).rstrip(),'```','']
    for label,key in [('Integration commit','integration'),('Proposed final merge message','merge')]:
        rows+=['## '+label,'','```text',message(data[key]).rstrip(),'```','']
    rows+=['## Application PR title','',data['pr_title'],'']
    return '\n'.join(rows)


def write_preview(json_path,markdown_path,data):
    """Reserve both draft names exclusively before writing either preview."""
    outputs=[(json_path,json.dumps(data,indent=2)+'\n'),(markdown_path,markdown(data))]
    with contextlib.ExitStack() as opened:
        created=[]
        try:
            for path,text in outputs:
                stream=opened.enter_context(path.open('x',encoding='utf-8'))
                created.append((path,stream,os.fstat(stream.fileno()),text))
        except BaseException:
            # Remove only our still-empty, untouched reservation. Keep handles
            # open during this check, and preserve replacements or owner edits.
            for path,stream,initial,text in reversed(created):
                try:current=path.lstat()
                except FileNotFoundError:continue
                fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
                if stat.S_ISREG(current.st_mode) and all(getattr(current,k)==getattr(initial,k) for k in fields):
                    path.unlink()
            raise
        for path,stream,initial,text in created:stream.write(text)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--json',type=Path,required=True);p.add_argument('--markdown',type=Path,required=True)
    a=p.parse_args();raw=a.plan.read_bytes();data=preview(json.loads(raw),hashlib.sha256(raw).hexdigest());validate(data,json.loads(raw),hashlib.sha256(raw).hexdigest())
    write_preview(a.json,a.markdown,data)
