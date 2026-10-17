#!/usr/bin/env bash
# 建一个固定的自签名代码签名证书（只需跑一次）。
#
# 为什么需要它：
#   ad-hoc 签名（codesign --sign -）的 designated requirement 是**纯二进制哈希**
#   `cdhash H"..."`。TCC 的「辅助功能」授权会把这条件存进库，于是**每次重新打包
#   旧授权就永久失效**，但设置里的开关还留在 ON —— 表现就是「明明授权了还说没授权」。
#
#   换成固定证书签名后，DR 变成「bundle id + 证书指纹」，重打包不变，
#   授权只需要勾一次。
#
# 用法：
#   ./packaging/macos/make-signing-cert.sh          # 建证书
#   ./packaging/macos/build.sh                      # 之后自动用它签名
#
# 过程中系统可能弹一次钥匙串授权框，点「允许」即可。

set -euo pipefail

NAME="${1:-dualswitch Self Signed}"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"
PASS="dualswitch-local"

if /usr/bin/security find-identity -v -p codesigning 2>/dev/null | grep -qF "$NAME"; then
    echo "[ok] 证书已存在：$NAME（跳过创建）"
    exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== 1/3 生成自签名证书（codeSigning）=="
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "$TMP/key.pem" -out "$TMP/cert.pem" \
    -subj "/CN=$NAME" \
    -addext "keyUsage=critical,digitalSignature" \
    -addext "extendedKeyUsage=critical,codeSigning" \
    -addext "basicConstraints=critical,CA:false" 2>/dev/null

echo "== 2/3 导入登录钥匙串（允许 codesign 使用私钥）=="
# OpenSSL 3 默认用 SHA256 做 PKCS#12 的 MAC，macOS 的 security 只认旧算法，
# 直接导入会报 "MAC verification failed"。优先用 -legacy，老版本 openssl 没有该选项就忽略。
if ! openssl pkcs12 -export -legacy -out "$TMP/cert.p12" -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
        -passout "pass:$PASS" -name "$NAME" 2>/dev/null; then
    openssl pkcs12 -export -out "$TMP/cert.p12" -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
        -passout "pass:$PASS" -name "$NAME" 2>/dev/null
fi
/usr/bin/security import "$TMP/cert.p12" -k "$KEYCHAIN" -P "$PASS" \
    -T /usr/bin/codesign -T /usr/bin/security >/dev/null

echo "== 3/3 标记为可信的代码签名证书 =="
# 免交互尝试；被系统拦下时给出提示，用户可在「钥匙串访问」里手动设置「始终信任」
if ! /usr/bin/security add-trusted-cert -p codeSign -k "$KEYCHAIN" "$TMP/cert.pem" 2>/dev/null; then
    echo "[warn] 自动设置信任失败：打开「钥匙串访问」→ 找到「$NAME」→ 显示简介 →"
    echo "       「信任」→「代码签名」选「始终信任」。否则签名可能校验不过。"
fi

if /usr/bin/security find-identity -v -p codesigning 2>/dev/null | grep -qF "$NAME"; then
    echo "[ok] 就绪：$NAME"
    echo "     现在跑 ./packaging/macos/build.sh 就会用它签名（重打包不用重新授权）"
else
    echo "[error] 没能在钥匙串里看到可用身份：$NAME" >&2
    exit 1
fi
