#!/usr/bin/env python3
"""Формирует воспроизводимые панели Monium; облако не изменяет."""
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

PROJECT = 'folder__b1gk1vp06cum61i9d4fe'
FOLDER = 'b1gk1vp06cum61i9d4fe'
SERVICES = ['sheets-api', 'less-accounts-api', 'control-plane-api', 'backoffice-api',
            'accounts-backoffice-reader', 'content-backoffice-reader', 'control-plane-backoffice-reader']
BASE_URL = f'https://monium.yandex.cloud/projects/{PROJECT}/dashboards/'
OUT = Path(__file__).with_name('dashboards')
OUT.mkdir(exist_ok=True)

def select(metric='http.server.requests', service='*', route=None, status=None, probes=False):
    tags = [f'project="{PROJECT}"', 'cluster="production"', f'service="{service}"', f'name="{metric}"']
    if route:
        tags.append(f'"http.route"="{route}"')
    elif not probes:
        tags.append('"http.route"!="/health|/ready"')
    if status:
        tags.append(f'"http.response.status_code"="{status}"')
    return '{' + ', '.join(tags) + '}'

def rate(metric='http.server.requests', group=None, **kw):
    source = f'non_negative_derivative({select(metric, **kw)})'
    return f'series_sum({json.dumps(group)}, {source})' if group else f'series_sum({source})'

def alias(query, name):
    return f'alias({query}, {json.dumps(name, ensure_ascii=False)})'

def percentile(n, service='*', route=None):
    # histogram_percentile combines all input series: filter a service before applying it.
    buckets = f'series_sum("bin", non_negative_derivative({select("http.server.request.duration", service, route)}))'
    return f'1000 * histogram_percentile({n}, "bin", {buckets})'

def chart(key, title, queries, x, y, w=18, h=9, unit='UNIT_REQUESTS_PER_SECOND', kind='LINE', desc='', colors=None, heat=False):
    data_id = str(uuid5(NAMESPACE_URL, key + ':source'))
    visual = {'type': 'VISUALIZATION_TYPE_' + kind, 'interpolate': 'INTERPOLATE_LEFT',
              'colorSchemeSettings': {'standard': {}},
              'yaxisSettings': {'left': {'type': 'YAXIS_TYPE_LINEAR', 'min': '0', 'unitFormat': unit}}}
    if heat:
        # Тип 9 — плитки: проверен сохранением через текущий интерфейс Monium.
        # В опубликованной protobuf-схеме пока нет имени для этого значения.
        visual.update({'type': 9, 'aggregation': 'SERIES_AGGREGATION_MAX',
                       'heatmapSettings': {'greenValue':'0', 'yellowValue':'250', 'redValue':'1000', 'violetValue':'3000'},
                       'colorSchemeSettings': {'gradient': {'greenValue':'0', 'yellowValue':'250', 'redValue':'1000', 'violetValue':'3000'}},
                       'tilesSettings': {'showTitle':True, 'showValue':True, 'showSparkline':True, 'sortField':'VALUE', 'sortOrder':'DESC'}})
    widget = {'id': str(uuid5(NAMESPACE_URL, key)), 'title':title, 'description':desc, 'displayLegend':True,
              'targets':[{'monitoringTarget':{'dataSourceId':data_id, 'query':q, 'textMode':True, 'name':chr(65+i)}} for i,q in enumerate(queries)],
              'dataSources':[{'monitoringDataSource':{'id':data_id, 'downsampling':{'gridAggregation':'GRID_AGGREGATION_LAST', 'gapFilling':'GAP_FILLING_NULL'}}}],
              'visualizationSettings':visual}
    if colors:
        widget['seriesOverrides'] = [{'targetIndex':str(i), 'settings':{'color':c}} for i,c in enumerate(colors)]
    return {'position':{'x':x,'y':y,'w':w,'h':h}, 'multiSourceChart':widget}

def text(message,y=0,h=4):
    return {'position':{'x':0,'y':y,'w':36,'h':h}, 'text':{'text':message}}

def dashboard(name,title,widgets,parameters=None):
    obj={'folderId':FOLDER,'name':name,'title':title,'description':'Промышленный Less. HTTP-метрики семи служб; обновление раз в минуту.',
         'labels':{'product':'less','environment':'production'},'widgets':widgets,
         'timeline':{'period':'1h','refreshInterval':60000},'parametrization':{'parameters':parameters or []}}
    (OUT / (name+'.json')).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    return obj

note = ('**Production · 7 служб · обновление 1 мин.** [Подробности HTTP]('+BASE_URL+'less-http-details/view) · [Машины]('+BASE_URL+'less-infrastructure/view)\n\n'
        'RPS — принятые службами запросы в секунду, включая внутренние вызовы; это не число действий пользователя. '
        '`/health` и `/ready` исключены из нагрузки и задержек. Разрыв — нет данных; пустой график 4xx/5xx может означать, '
        'что такая серия ещё не возникала: сверяйте с проверками доступности ниже. Цветовые пороги задержек — ориентиры, не SLA.')
widgets=[text(note,h=4),
 chart('health-rps','Нагрузка · RPS по службам',[alias(rate(group='service'),'{{service}}')],0,4,desc='Производная каждого накопительного счётчика вычисляется до суммирования. При сбросе — разрыв, не отрицательный RPS.'),
 chart('health-percentiles','Время ответа · p50 / p95 / p99',[alias(percentile(n),f'p{n}') for n in [50,95,99]],18,4,unit='UNIT_MILLISECONDS',desc='Оценка процентилей по приросту гистограммы между отправками. Внутренние вызовы включены; при малом числе запросов значения менее устойчивы.'),
 chart('health-2xx','2xx · успешные ответы / с',[alias(rate(group='service',status='2*'),'{{service}}')],0,13,w=12,colors=['#35b37e']),
 chart('health-4xx','4xx · ошибки клиента / с',[alias(rate(group='service',status='4*'),'{{service}}'), alias('0 * '+rate(), 'Нулевая линия · телеметрия поступает')],12,13,w=12,colors=['#f5b942'],desc='В том числе ожидаемые 401/403. Пусто: серия ещё не возникала или нет телеметрии — смотрите проверки доступности.'),
 chart('health-5xx','5xx · ошибки сервера / с',[alias(rate(group='service',status='5*'),'{{service}}'), alias('0 * '+rate(), 'Нулевая линия · телеметрия поступает')],24,13,w=12,colors=['#ef5350'],desc='Прикладные ошибки Less используют HTTP 500. Пусто: серия ещё не возникала или нет телеметрии.'),
 chart('health-latency-map','Карта задержек · пик p95 по интервалам',[alias(percentile(95,s),s) for s in SERVICES],0,22,w=36,h=8,unit='UNIT_MILLISECONDS',heat=True,desc='Плитка на службу. Максимум p95 по интервалам выбранного периода; это не p95 всего периода. При увеличении периода интервалы укрупняются. Без запросов оценка может быть 0. 250 мс — жёлтый, 1 с — красный, 3 с — фиолетовый.'),
 chart('health-statuses','Ответы · все коды HTTP',[alias(rate(group='http.response.status_code'),'HTTP {{http.response.status_code}}')],0,30,kind='STACK'),
 chart('health-mean','Среднее время ответа по службам',[alias('1000 * '+rate('http.server.request.duration.sum',group='service')+' / '+rate('http.server.request.duration.count',group='service'),'{{service}}')],18,30,unit='UNIT_MILLISECONDS',desc='Сумма длительностей новых запросов / число новых запросов. При отсутствии запросов значения нет.'),
 chart('health-probes','Проверки доступности · запросов в минуту',[alias('60 * '+rate(group='service',route='/health|/ready',probes=True,status='2*'),'{{service}}')],0,39,w=36,h=8,unit='UNIT_NONE',desc='Ожидаются семь линий. Пропавшая линия требует проверки службы или доставки телеметрии. Это успешность внутренних проб, не внешняя доступность less.place.'),
 text('**Границы наблюдения.** Сейчас измерены входящие HTTP-запросы после прокси. Браузер, nginx, SQL, очереди, S3, SMTP и OAuth не имеют отдельных измерений. Панель не заменяет внешнюю проверку сайта и оповещения.',47,3)]
dashboard('less-production-health','Less · 01 · Здоровье продукта',widgets)

svc='{{service}}';route='{{route}}'
params=[{'name':'service','title':'Служба','custom':{'values':['*']+SERVICES,'defaultValues':['sheets-api']},'description':'* — все службы. Внутренние вызовы учитываются отдельно.'},
        {'name':'route','title':'Маршрут','text':{'defaultValue':'*'},'description':'Шаблон маршрута, например /api/pages/{id}/snapshot. * — все, включая проверки доступности.'}]
# В подробном разборе разрешён любой маршрут, включая проверки доступности.
def filtered(metric='http.server.requests',status=None):
    return select(metric,svc,route,status)
def frate(metric='http.server.requests',group=None,status=None):
    src=f'non_negative_derivative({filtered(metric,status)})'
    return f'series_sum({json.dumps(group)}, {src})' if group else f'series_sum({src})'
def fp(n):return f'1000 * histogram_percentile({n}, "bin", series_sum("bin", non_negative_derivative({filtered("http.server.request.duration")})))'
slow_mean='1000 * '+frate('http.server.request.duration.sum',['service','http.request.method','http.route'])+' / '+frate('http.server.request.duration.count',['service','http.request.method','http.route'])
bins=f'series_sum("bin", non_negative_derivative({filtered("http.server.request.duration")}))'
band_queries=[alias(f'histogram_count({a}, {b}, "bin", {bins})',name) for a,b,name in [(0,.01,'до 10 мс'),(.01,.05,'10–50 мс'),(.05,.1,'50–100 мс'),(.1,.25,'100–250 мс'),(.25,1,'250 мс–1 с'),(1,10,'1–10 с')]]
band_queries.append(alias(f'histogram_count("bin", {bins}) - histogram_count(0, 10, "bin", {bins})', 'более 10 с'))

widgets=[text('**Разбор HTTP.** [Общий обзор]('+BASE_URL+'less-production-health/view) · Выберите службу и при необходимости маршрут. '
              'Здесь * включает проверки доступности; общий обзор их исключает. Диапазоны времени показывают запросы в секунду. Максимум снизу накоплен с запуска процесса и помечен отдельно. '
              'Пустые 4xx/5xx не заменяют проверку доставки данных.',h=3),
 chart('detail-rps','RPS · наиболее нагруженные маршруты',[alias('top_max(10, '+frate(group=['service','http.request.method','http.route'])+')','{{service}} · {{http.request.method}} {{http.route}}')],0,3),
 chart('detail-percentile','Время ответа · p50 / p95 / p99',[alias(fp(n),f'p{n}') for n in [50,95,99]],18,3,unit='UNIT_MILLISECONDS'),
 chart('detail-bands','Распределение задержек · запросов / с',band_queries,0,12,w=36,h=10,kind='STACK',desc='Непересекающиеся диапазоны длительности. Прирост накопительных бакетов, не распределение с запуска процесса.'),
 chart('detail-slow','Медленные маршруты · среднее время',[alias('top_max(10, '+slow_mean+')','{{service}} · {{http.request.method}} {{http.route}}')],0,22,unit='UNIT_MILLISECONDS'),
 chart('detail-status','Коды HTTP · запросов / с',[alias(frate(group='http.response.status_code'),'HTTP {{http.response.status_code}}')],18,22,kind='STACK'),
 chart('detail-4xx','4xx · маршруты и коды',[alias('top_max(10, '+frate(group=['service','http.request.method','http.route','http.response.status_code'],status='4*')+')','{{http.response.status_code}} · {{service}} · {{http.request.method}} {{http.route}}')],0,31),
 chart('detail-5xx','5xx · маршруты и коды',[alias('top_max(10, '+frate(group=['service','http.request.method','http.route','http.response.status_code'],status='5*')+')','{{http.response.status_code}} · {{service}} · {{http.request.method}} {{http.route}}')],18,31),
 chart('detail-max','Исторический максимум · с запуска процесса',[alias('top_max(10, 1000 * '+filtered('http.server.request.duration.max')+')','{{service}} · {{http.request.method}} {{http.route}} · {{http.response.status_code}}')],0,40,w=36,unit='UNIT_MILLISECONDS',desc='Накопительный максимум! Старый медленный запрос остаётся здесь до перезапуска процесса. Для текущих задержек используйте процентили выше.')]
dashboard('less-http-details','Less · 02 · Запросы и задержки',widgets,params)



def compute(metric, group='resource_id', disk=False):
    sel = '{project="'+PROJECT+'", cluster="default", service="__compute__", name="'+metric+'"}'
    return alias('series_sum('+json.dumps(group)+', '+sel+')','{{'+group+'}}')

widgets=[text('**Машины Less.** [Общий обзор]('+BASE_URL+'less-production-health/view) · p-common-1 и p-content-shard-1. '
              'Метрики платформы уже выражены в процентах или в секунду; повторная производная не применяется. '
              'Использование RAM и свободное место в файловых системах требуют отдельного агента внутри ОС.',h=3),
 chart('infra-cpu','Процессор · загрузка ВМ',[compute('cpu_usage')],0,3,unit='UNIT_PERCENT'),
 chart('infra-cpu-cores','Процессор · отдельные ядра',[alias('{project="'+PROJECT+'", cluster="default", service="__compute__", name="cpu_utilization"}', '{{resource_id}} · {{cpu_name}}')],18,3,unit='UNIT_PERCENT'),
 chart('infra-network-in','Сеть · входящий поток',[compute('network_received_bytes')],0,12,unit='UNIT_BYTES_SI_PER_SECOND'),
 chart('infra-network-out','Сеть · исходящий поток',[compute('network_sent_bytes')],18,12,unit='UNIT_BYTES_SI_PER_SECOND'),
 chart('infra-disk-read','Диск · чтение',[compute('disk.read_bytes','instance')],0,21,unit='UNIT_BYTES_SI_PER_SECOND'),
 chart('infra-disk-write','Диск · запись',[compute('disk.write_bytes','instance')],18,21,unit='UNIT_BYTES_SI_PER_SECOND'),
 chart('infra-disk-iops','Диск · операции чтения и записи',[alias('{project="'+PROJECT+'", cluster="default", service="__compute__", name="disk.read_ops|disk.write_ops"}','{{instance}} · {{name}}')],0,30,unit='UNIT_IO_OPERATIONS_PER_SECOND'),
 chart('infra-disk-errors','Диск · ошибки операций',[alias('{project="'+PROJECT+'", cluster="default", service="__compute__", name="disk.read_errors|disk.write_errors"}','{{instance}} · {{name}}')],18,30,unit='UNIT_OPERATIONS_PER_SECOND')]
dashboard('less-infrastructure','Less · 03 · Машины и ресурсы',widgets)
print('Built', len(list(OUT.glob('*.json'))), 'dashboard definitions')
