# iStudy dev.61 增量映像（PHP API）：以 Server 上目前的 dev.60 映像為基底，只替換 PHP 程式碼與 SQL（含 migration 0008）。
# 不執行 apt、不需要網路；PHP 版本與擴充套件與基底映像相同。
ARG BASE=istudy/php:1.0.0-dev.60
FROM ${BASE}
ARG BASE
# PHP 的警告不寫進 API 回應（避免破壞 JSON、暴露路徑），改記在容器 log（docker logs istudy-php-1）
RUN rm -rf /var/www/html/public /var/www/html/src /var/www/html/cli /var/www/html/sql \
 && printf 'display_errors=Off\nlog_errors=On\n' > /usr/local/etc/php/conf.d/zz-istudy-errors.ini
COPY server/public /var/www/html/public
COPY server/src /var/www/html/src
COPY server/cli /var/www/html/cli
COPY server/sql /var/www/html/sql
LABEL tw.istudy.version="1.0.0-dev.61" tw.istudy.base="${BASE}"
