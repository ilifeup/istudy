# iStudy Python Agent：FFmpeg（含 Quick Sync；dev.59 起含 oneVPL GPU Runtime libmfx-gen1.2）、Intel iHD 驅動、LibreOffice、Noto CJK、
# Office 等寬字型（dev.60：Carlito、Caladea、Liberation）、exiftool、
# PostgreSQL 18 用戶端（pg_dump／psql：主機是 PostgreSQL 18，pg_dump 版本不能比伺服器舊）、sherpa-onnx（FunASR）、OpenCC。
# dev.59 起不安裝 faster-whisper（Whisper 引擎已移除）。dev.62 起內含 age（加密備份）。
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 TZ=Asia/Taipei PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN set -eux; \
    sed -i 's/^Components: main restricted$/Components: main restricted universe multiverse/' /etc/apt/sources.list.d/ubuntu.sources || true; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg tzdata \
        python3 python3-venv libpq5 ffmpeg vainfo intel-media-va-driver-non-free libvpl2 libmfx-gen1.2 \
        libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-core fonts-noto-cjk \
        fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation \
        libimage-exiftool-perl; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt noble-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-client-18; \
    apt-get purge -y --auto-remove curl gnupg; \
    rm -rf /var/lib/apt/lists/*
# dev.60：Office 轉 PDF 的字型（Carlito／Caladea／Liberation 與 Calibri／Cambria／Arial／Times 等寬，欄寬與分頁才會和 Excel 一致；
#        繁體中文 Office 字型名稱對應到 Noto CJK TC）
COPY agent/fontconfig/64-istudy-cjk-tc.conf /etc/fonts/conf.d/64-istudy-cjk-tc.conf
RUN fc-cache -f >/dev/null
# dev.62：age v1.2.1（官方 GitHub release 的 linux-amd64 靜態執行檔，隨原始碼附在 deploy/vendor，建置時核對 SHA-256，不需要連網）
#        Agent 只用公鑰加密備份；主機 istudyctl 以這個映像的一次性容器解密驗證（私鑰經標準輸入傳入，不掛載）
COPY deploy/vendor/age-v1.2.1-linux-amd64.tar.gz /tmp/age.tar.gz
RUN echo "7df45a6cc87d4da11cc03a539a7470c15b1041ab2b396af088fe9990f7c79d50  /tmp/age.tar.gz" | sha256sum -c - \
 && tar -xzf /tmp/age.tar.gz -C /tmp && install -m 0755 /tmp/age/age /tmp/age/age-keygen /usr/local/bin/ \
 && install -d /usr/share/doc/age && install -m 0644 /tmp/age/LICENSE /usr/share/doc/age/LICENSE \
 && rm -rf /tmp/age /tmp/age.tar.gz && age --version
COPY agent/requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
RUN groupadd -g 10001 istudy && useradd -u 10001 -g 10001 -m -d /home/istudy -s /usr/sbin/nologin istudy
COPY agent/istudy_common /opt/istudy-agent/istudy_common
COPY agent/worker /opt/istudy-agent/worker
ENV PATH=/opt/venv/bin:$PATH HOME=/home/istudy
WORKDIR /opt/istudy-agent
USER 10001:10001
CMD ["python3", "-m", "worker"]
