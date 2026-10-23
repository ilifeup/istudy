# iStudy PHP API：PHP 8.3 FPM＋pdo_pgsql（程式碼放進映像，離線部署時以 docker save／load 匯入）
FROM php:8.3.26-fpm-bookworm
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libpq5 libpq-dev; \
    docker-php-ext-install pdo_pgsql; \
    apt-get purge -y --auto-remove libpq-dev; \
    rm -rf /var/lib/apt/lists/*
RUN { \
      echo 'expose_php=Off'; \
      echo 'memory_limit=256M'; \
      echo 'post_max_size=16M'; \
      echo 'upload_max_filesize=16M'; \
      echo 'max_execution_time=120'; \
      echo 'date.timezone=Asia/Taipei'; \
      echo 'opcache.enable=1'; \
      echo 'opcache.validate_timestamps=0'; \
      echo 'display_errors=Off'; \
      echo 'log_errors=On'; \
    } > /usr/local/etc/php/conf.d/istudy.ini; \
    { \
      echo '[www]'; \
      echo 'pm = dynamic'; \
      echo 'pm.max_children = 24'; \
      echo 'pm.start_servers = 4'; \
      echo 'pm.min_spare_servers = 2'; \
      echo 'pm.max_spare_servers = 8'; \
      echo 'clear_env = no'; \
    } > /usr/local/etc/php-fpm.d/zz-istudy.conf
COPY server/public /var/www/html/public
COPY server/src /var/www/html/src
COPY server/cli /var/www/html/cli
COPY server/sql /var/www/html/sql
ENV ISTUDY_SRC=/var/www/html/src ISTUDY_SQL=/var/www/html/sql
WORKDIR /var/www/html
