#!/usr/bin/env python3
"""Compile the actual added RFFI proxy mapping in a small host harness.

This does not run WebRTC or exercise real network connections.
"""
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
lines = (HERE / 'webrtc-8037b-molly-proxy-UNTESTED.patch').read_text().splitlines()
start = next(i for i, line in enumerate(lines) if line == '+  webrtc::ProxyInfo proxy_info;')
end = next(i for i in range(start, len(lines)) if lines[i] == '+  config.proxy_info = proxy_info;')
body = '\n'.join(line[1:] for line in lines[start:end + 1])
needle = '      RTC_LOG(LS_ERROR) << "Unknown proxy type: " << proxy_info_borrowed->type;'
assert body.count(needle) == 1

prefix = r'''
namespace std { struct string { string() {} string(const char*) {} }; }
struct Log { template <typename T> Log& operator<<(const T&) { return *this; } };
enum RffiProxyType { kRffiProxyTypeNone = 0, kRffiProxyTypeHttps = 1, kRffiProxyTypeSocks5 = 2 };
struct RffiProxyInfo { int type; const char* hostname_borrowed; int port; const char* username_borrowed; const char* password_borrowed; };
namespace webrtc { enum ProxyType { PROXY_NONE, PROXY_HTTPS, PROXY_SOCKS5 }; struct ProxyInfo { ProxyType type = PROXY_NONE; int address = 0; std::string username; std::string password; }; struct InsecureCryptStringImpl { std::string value; std::string& password() { return value; } }; }
using webrtc::InsecureCryptStringImpl;
int SocketAddress(std::string, int port) { return port; }
std::string CryptString(const InsecureCryptStringImpl& p) { return p.value; }
#define RTC_LOG(level) Log()
#define LS_ERROR 0
struct Config { webrtc::ProxyInfo proxy_info; };
void* map_proxy(const RffiProxyInfo* proxy_info_borrowed, Config& config) {
'''
suffix = r'''
  return &config;
}
int main() {
  RffiProxyInfo input{0, "proxy.example", 443, "name", "password"}; Config config;
  for (int type = 0; type < 3; ++type) {
    input.type = type;
    if (!map_proxy(&input, config) || config.proxy_info.type != type) return 10 + type;
  }
  input.type = 999;
  config.proxy_info.type = webrtc::PROXY_SOCKS5;
  if (map_proxy(&input, config) != nullptr) return 20;
  if (config.proxy_info.type != webrtc::PROXY_SOCKS5) return 21;
  return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='proxy-type-') as tmp:
    root = Path(tmp)
    fixture = root / 'ringrtc/rffi/src/peer_connection_factory.cc'
    fixture.parent.mkdir(parents=True)
    fixture.write_text(body + '\n')
    fix = str(HERE / 'webrtc-unknown-proxy-fix.patch')
    for name, expected in [('original', 20), ('fixed', 0)]:
        if name == 'fixed':
            subprocess.run(['git', 'apply', '--check', fix], cwd=root, check=True)
            subprocess.run(['git', 'apply', fix], cwd=root, check=True)
        content = fixture.read_text()
        cpp = root / (name + '.cc')
        exe = root / name
        cpp.write_text(prefix + content + suffix)
        subprocess.run(['clang++', '-std=c++17', '-nostdlib++', str(cpp), '-o', str(exe)], check=True)
        result = subprocess.run([str(exe)], check=False)
        if result.returncode != expected:
            raise SystemExit(f'{name}: expected {expected}, got {result.returncode}')
        print(f'{name}: exit {result.returncode} as expected')
