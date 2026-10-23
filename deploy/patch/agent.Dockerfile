# iStudy dev.62 增量映像（Agent）：以 Server 上目前的 dev.61 映像為基底，替換 Agent 程式碼，並加入 age（加密備份）。
# 不執行 apt、不需要網路：age 用隨原始碼附的官方 v1.2.1 靜態執行檔（deploy/vendor），建置時核對 SHA-256。
# Office 字型、libmfx-gen1.2、sherpa-onnx、FFmpeg、iHD 驅動都沿用基底。
ARG BASE=istudy/agent:1.0.0-dev.61
FROM ${BASE}
ARG BASE
USER root
RUN test "$(fc-match -f '%{family[0]}' Calibri)" = Carlito \
 && test "$(fc-match -f '%{family[0]}' 新細明體)" = "Noto Serif CJK TC" \
 && ls /usr/lib/x86_64-linux-gnu/libmfx-gen.so.1.2* >/dev/null \
 && /opt/venv/bin/python -c "import importlib.util as u, sys, sherpa_onnx; sys.exit('faster_whisper 不應存在' if u.find_spec('faster_whisper') else 0)" \
 && ffmpeg -hide_banner -decoders 2>/dev/null | grep -Eq ' (mp3|aac|flac|vorbis|opus|wmav2) ' \
 && echo "基底映像：dev.61 字型、libmfx-gen1.2、sherpa-onnx、FFmpeg 音訊解碼器都在"
COPY deploy/vendor/age-v1.2.1-linux-amd64.tar.gz /tmp/age.tar.gz
RUN echo "7df45a6cc87d4da11cc03a539a7470c15b1041ab2b396af088fe9990f7c79d50  /tmp/age.tar.gz" | sha256sum -c - \
 && tar -xzf /tmp/age.tar.gz -C /tmp && install -m 0755 /tmp/age/age /tmp/age/age-keygen /usr/local/bin/ \
 && install -d /usr/share/doc/age && install -m 0644 /tmp/age/LICENSE /usr/share/doc/age/LICENSE \
 && rm -rf /tmp/age /tmp/age.tar.gz && age --version
RUN rm -rf /opt/istudy-agent/istudy_common /opt/istudy-agent/worker
COPY agent/istudy_common /opt/istudy-agent/istudy_common
COPY agent/worker /opt/istudy-agent/worker
RUN /opt/venv/bin/python -c "import sys; sys.path.insert(0, '/opt/istudy-agent'); from worker import main; assert {'purge_media', 'outgo_asr', 'backup'} <= set(main.HANDLERS); from istudy_common import backupcrypt, agepass" \
 && test -f /opt/istudy-agent/istudy_common/agepass.py
USER 10001:10001
LABEL tw.istudy.version="1.0.0-dev.62" tw.istudy.base="${BASE}"
