#!/bin/sh
# Выполняет владелец либо агент после отдельного разрешения на изменение прав.
# ACL сейчас пусты. Разрешаем READ+WRITE только соответствующим сервисным учётным записям.
# Существующие bucket policy продолжают ограничивать конкретные операции.
set -eu

yc storage bucket update \
  --name less-accounts-prod \
  --grants grant-type=grant-type-account,grantee-id=aje90c1ml5nriiel02bg,permission=permission-read \
  --grants grant-type=grant-type-account,grantee-id=aje90c1ml5nriiel02bg,permission=permission-write

yc storage bucket update \
  --name less \
  --grants grant-type=grant-type-account,grantee-id=ajelmlmid6m5e92ha722,permission=permission-read \
  --grants grant-type=grant-type-account,grantee-id=ajelmlmid6m5e92ha722,permission=permission-write
