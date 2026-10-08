#!/usr/bin/env python3
"""Создаёт/обновляет только панели Less из соседнего каталога через официальный API.

Зависимость: yandexcloud. IAM-токен берётся из текущего профиля yc и не сохраняется.
Перед обновлением хранит предыдущую конфигурацию локально; etag защищает от гонки.
"""
import argparse
import json
import subprocess
from pathlib import Path
import grpc
from google.protobuf.json_format import ParseDict, MessageToDict
from yandex.cloud.monitoring.v3.dashboard_service_pb2 import (
    ListDashboardsRequest, GetDashboardRequest, CreateDashboardRequest, UpdateDashboardRequest,
)
from yandex.cloud.monitoring.v3.dashboard_service_pb2_grpc import DashboardServiceStub
from yandex.cloud.monitoring.v3.dashboard_pb2 import Dashboard

ROOT = Path(__file__).parent
FOLDER = 'b1gk1vp06cum61i9d4fe'
ALLOWED = {'less-production-health', 'less-http-details', 'less-infrastructure'}
parser = argparse.ArgumentParser()
parser.add_argument('--apply', action='store_true', help='Изменить панели в облаке; без флага только проверка JSON')
args = parser.parse_args()
definitions = [json.loads(p.read_text()) for p in sorted((ROOT / 'dashboards').glob('*.json'))]
for item in definitions:
    assert item['folderId'] == FOLDER and item['name'] in ALLOWED
    ParseDict(item, CreateDashboardRequest())
if not args.apply:
    print(f'Проверено определений: {len(definitions)}; облако не изменено.')
    raise SystemExit()
token = subprocess.check_output(['yc', 'iam', 'create-token'], text=True).strip()
metadata = [('authorization', 'Bearer ' + token)]
channel = grpc.secure_channel('monitoring.api.cloud.yandex.net:443', grpc.ssl_channel_credentials())
stub = DashboardServiceStub(channel)
listing = stub.List(ListDashboardsRequest(folder_id=FOLDER, page_size=1000), metadata=metadata, timeout=20)
assert not listing.next_page_token, 'Нужна обработка следующей страницы списка'
existing = {d.name:d.id for d in listing.dashboards}
backup = ROOT / 'dashboard-backups'
backup.mkdir(exist_ok=True)
result = []
for item in definitions:
    name = item['name']
    if name in existing:
        previous = stub.Get(GetDashboardRequest(dashboard_id=existing[name]), metadata=metadata, timeout=20)
        (backup / f'{name}-etag-{previous.etag}.json').write_text(json.dumps(MessageToDict(previous), ensure_ascii=False, indent=2)+'\n')
        update = {k:v for k,v in item.items() if k != 'folderId'}
        update.update({'dashboardId':previous.id, 'etag':previous.etag, 'comment':'Настройка технического обзора Less'})
        op = stub.Update(ParseDict(update, UpdateDashboardRequest()), metadata=metadata, timeout=30)
    else:
        op = stub.Create(ParseDict(item, CreateDashboardRequest()), metadata=metadata, timeout=30)
    if not op.done:
        raise RuntimeError('Операция ещё выполняется; сначала проверить её: '+op.id)
    if op.HasField('error'):
        raise RuntimeError(f'Операция {op.id}: код {op.error.code}, {op.error.message}')
    response = Dashboard()
    assert op.response.Unpack(response)
    confirmed = stub.Get(GetDashboardRequest(dashboard_id=response.id), metadata=metadata, timeout=20)
    assert len(confirmed.widgets) == len(item['widgets']) and confirmed.title == item['title']
    entry = {'id':confirmed.id,'name':name,'title':confirmed.title,'etag':confirmed.etag,
             'widgets':len(confirmed.widgets),
             'url':f'https://monium.yandex.cloud/projects/folder__{FOLDER}/dashboards/{name}/view'}
    result.append(entry)
    print(json.dumps(entry,ensure_ascii=False))
(ROOT / 'dashboard-manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
