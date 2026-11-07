#!/usr/bin/env python3
"""Exercise extracted production SOCKS request code with small stand-ins, not sockets."""
from pathlib import Path
import subprocess, tempfile
p = Path(__file__).resolve().parent
patch = (p / 'webrtc-8037b-molly-proxy-UNTESTED.patch').read_text()
start = patch.index('+void AsyncSocksProxySocket::SendConnect() {')
end = patch.index('+void AsyncSocksProxySocket::Error(', start)
method = '\n'.join(line[1:] for line in patch[start:end].splitlines() if line.startswith('+')) + '\n'
harness = r'''
typedef unsigned char uint8_t; typedef unsigned short uint16_t; typedef unsigned int uint32_t;
#define AF_INET 2
#define AF_INET6 10
#define SOCKET_EACCES 13
namespace std { struct string { const char* p; int size() const { int n=0; while(p[n]) n++; return n; } }; }
struct ByteBufferWriter {
 uint8_t bytes[512]; int n=0;
 void WriteUInt8(uint8_t x){bytes[n++]=x;}
 void WriteUInt16(uint16_t x){WriteUInt8(x>>8);WriteUInt8(x);}
 void WriteUInt32(uint32_t x){WriteUInt16(x>>16);WriteUInt16(x);}
 void WriteString(std::string x){for(int i=0;i<x.size();i++) WriteUInt8(x.p[i]);}
 const uint8_t* Data(){return bytes;} int Length(){return n;}
};
struct Address6 { uint8_t s6_addr[16]; };
struct Ip { Address6 a; Address6 ipv6_address() const {return a;} };
struct Dest { bool unresolved=false; int fam=AF_INET; uint32_t v4=0x01020304; Ip v6={};
 bool IsUnresolvedIP(){return unresolved;} std::string hostname(){return {"example.org"};}
 int family(){return fam;} uint32_t ip(){return fam==AF_INET?v4:0;} Ip ipaddr(){return v6;} uint16_t port(){return 443;}
};
struct AsyncSocksProxySocket { Dest dest_; int state_=0; enum {SS_CONNECT=1}; int error=0, n=0; uint8_t sent[512]={};
 void SendConnect(); void Error(int e){error=e;} void DirectSend(const uint8_t* b,int l){n=l;for(int i=0;i<l;i++)sent[i]=b[i];}
};
#include "rtc_base/socket_adapters.cc"
int main() {
 AsyncSocksProxySocket v4; v4.SendConnect();
 uint8_t a[]={5,1,0,1,1,2,3,4,1,187}; if(v4.n!=10)return 1; for(int i=0;i<10;i++)if(v4.sent[i]!=a[i])return 2;
 AsyncSocksProxySocket dns; dns.dest_.unresolved=true; dns.SendConnect(); if(dns.n!=18 || dns.sent[3]!=3 || dns.sent[4]!=11 || dns.sent[16]!=1 || dns.sent[17]!=187)return 3;
 const char* name="example.org"; for(int i=0;i<11;i++)if(dns.sent[5+i]!=name[i])return 4;
 AsyncSocksProxySocket v6; v6.dest_.fam=AF_INET6; uint8_t addr[16]={0x20,1,0x0d,0xb8,0,0,0,0,0,0,0,0,0,0,0,1};
 for(int i=0;i<16;i++)v6.dest_.v6.a.s6_addr[i]=addr[i]; v6.SendConnect(); if(v6.n!=22||v6.sent[3]!=4)return 5;
 for(int i=0;i<16;i++)if(v6.sent[4+i]!=addr[i])return 6; if(v6.sent[20]!=1||v6.sent[21]!=187)return 7;
 AsyncSocksProxySocket bad; bad.dest_.fam=42; bad.SendConnect(); if(!bad.error||bad.n||bad.state_)return 8;
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='proxy-ipv6-') as d:
 d=Path(d); (d/'rtc_base').mkdir(); (d/'rtc_base/socket_adapters.cc').write_text(method); (d/'test.cc').write_text(harness)
 def run(label, expected):
  subprocess.run(['clang++','-std=c++17','-nostdlib++',str(d/'test.cc'),'-o',str(d/'test')],check=True)
  result=subprocess.run([str(d/'test')]).returncode
  print(f'{label}: exit {result} (expected {expected})')
  if result!=expected: raise SystemExit(1)
 run('original',5)
 subprocess.run(['git','apply','--check',str(p/'webrtc-socks-ipv6-fix.patch')],cwd=d,check=True)
 subprocess.run(['git','apply',str(p/'webrtc-socks-ipv6-fix.patch')],cwd=d,check=True)
 run('fixed',0)
