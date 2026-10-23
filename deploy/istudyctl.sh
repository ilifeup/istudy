#!/bin/sh
# /usr/local/sbin/istudyctl：polkit 規則只允許 pkexec 執行這一支程式。
# -I：隔離模式，不讀取使用者的 site-packages、PYTHONPATH 等環境設定。
exec /usr/bin/python3 -I /opt/istudy/agent/istudyctl.py "$@"
