---
title: SLO, SLI, error budget и алертинг, который не будит зря
tags: [observability, sre, alerting, grafana, prometheus]
learned: 2026-10-27
---
## Кратко
**SLI** — это измеряемое отношение хороших событий ко всем событиям (успешные запросы / все запросы; запросы быстрее 300 мс / все запросы). **SLO** — целевое значение этого отношения за окно (99.9 % за 30 дней). **Бюджет ошибок (error budget)** — то, что остаётся (0.1 % запросов, примерно 43 минуты полного простоя в месяц), и он тратится на инциденты, рискованные деплои и эксперименты. Алерты должны срабатывать на **симптомы**, которые чувствуют пользователи (бюджет сгорает слишком быстро, падает доступность), а не на причины (CPU 80 %, перезапустился один pod); сигналам уровня причин место на дашбордах. Пейдж должен быть actionable, редким и срочным; всё остальное — тикет.

## Основное

### Словарь
| Термин | Значение | Пример |
|---|---|---|
| SLI | Индикатор уровня сервиса (service level indicator), измеряемое отношение | `sum(rate(http_requests_total{status!~"5.."}[5m])) / sum(rate(http_requests_total[5m]))` |
| SLO | Цель для SLI за окно | 99.9 % запросов на активацию успешны за 30 дней |
| SLA | Договор с последствиями (компенсации) | 99.5 % в месяц, задаётся мягче, чем SLO |
| Error budget | `1 - SLO` от событий окна | 0.1 % от ~30 млн запросов = 30 тыс. неудачных запросов в месяц |
| Burn rate | Насколько быстро тратится бюджет по сравнению с режимом «ровно в цель» | Burn 1 = бюджета хватает на всё окно; burn 14.4 = кончится примерно за 2 дня |
- Выбирай SLI с точки зрения пользователя: доступность (не 5xx), задержка (ниже порога), корректность (активация действительно произошла, измеряется пробой), свежесть для конвейеров данных (лаг меньше N минут, [[kafka-consumer]]).
- Измеряй на границе (шлюз, [[load-balancers-l4-vs-l7|балансировщик нагрузки]]) или синтетической пробой, а не внутри сервиса, который сам может оказаться упавшим.

### Бюджет ошибок как инструмент принятия решений
- Бюджет в порядке: выпускай фичи, проводи chaos-тесты, деплой в пятницу. Бюджет сгорает или исчерпан: заморозь рискованные изменения, приоритизируй работу над надёжностью. Это превращает вопрос «достаточно ли надёжна система» из спора в число, которое принимают и разработчики, и продукт.
- 100 % — не цель: каждая следующая девятка стоит примерно в 10 раз дороже, а каждая зависимость (облако, DNS, партнёр SM-DP+) всё равно ограничивает тебя сверху.

### Алертинг по симптомам и burn rate с несколькими окнами
- Алерты по причинам (`cpu > 80%`, `pod restarted`, `disk 70%`) срабатывают часто, нередко безвредны и приучают людей игнорировать пейджи. Алерты по симптомам (`error budget burn rate > 14.4 over 1 h AND over 5 m`) срабатывают, когда пользователи затронуты, и перестают, когда нет.
- **Несколько окон и несколько порогов burn rate (multi-window, multi-burn-rate)** (Google SRE workbook): пейдж при burn rate 14.4 за 1 ч (и всё ещё за 5 м, чтобы избежать флаппинга), пейдж при 6 за 6 ч, тикет при 1 за 3 дня. Короткое окно подтверждает, что это всё ещё происходит; длинное окно задаёт серьёзность.
- Оставь несколько алертов по причинам там, где симптом приходит слишком поздно: диск заполнится через 4 ч (`predict_linear`), сертификат истекает через 7 дней, растёт лаг Kafka consumer, слот репликации Postgres не потребляется ([[outbox-pattern]]), в DLT приходят сообщения ([[dead-letter-queues-and-retries]]).

### Гигиена алертов
- У каждого алерта есть: серьёзность (пейдж или тикет), владелец, ссылка на runbook и ссылка на дашборд. Если никто не может сказать, что делать при срабатывании, удали его.
- Метки `severity`, `service`, `team` маршрутизируют его: пейджи — дежурному (Telegram/PagerDuty), тикеты — в канал. Мост Sentry→Telegram — та же идея для исключений ([[error-tracking-with-sentry]], [[sebep-incident-analysis]]).
- Длительность `for:` и `keep_firing_for` останавливают флаппинг; группировка (`group_by: [service]`) не даёт 40 pods создать 40 пейджей; подавление (inhibition) глушит дочерние алерты, когда срабатывает родительский (упал весь кластер → не пейджить по каждому сервису).
- Ревью раз в месяц: алерты, которые сработали, но никто ничего не сделал → удалить или понизить; инциденты без алерта → добавить алерт по симптому.

### Где это работает в стеке Grafana
- Единый алертинг Grafana (unified alerting) по расписанию вычисляет PromQL против Mimir (и LogQL против Loki), группирует через встроенный Alertmanager и уведомляет через точки контакта (Telegram, email, webhook, OnCall). Правила алертов можно разворачивать из YAML-файлов или через API, поэтому они живут в git рядом с дашбордами ([[grafana-dashboards-and-red-use]]).
- Recording rules заранее вычисляют отношения SLI (`sli:activation:success_ratio_5m`), чтобы запросы алертов и дашбордов оставались дешёвыми.

## Пример
Правило алерта, развёрнутое через provisioning в Grafana: пейдж о быстром сгорании бюджета для SLO доступности 99.9 % API активации, с парой окон 1 ч / 5 м и аннотацией с runbook.

```yaml
# grafana/provisioning/alerting/esim-slo.yaml
apiVersion: 1
groups:
  - orgId: 1
    name: esim-slo
    folder: eSIM
    interval: 1m
    rules:
      - uid: esim-activation-fast-burn
        title: eSIM activation availability: fast error-budget burn
        condition: C
        for: 2m
        labels: { severity: page, service: esim-provisioning, team: esim-backend }
        annotations:
          summary: "Activation API burning error budget at >14.4x (1h and 5m windows)"
          runbook_url: https://wiki.internal/runbooks/esim-activation-availability
          dashboard_uid: esim-service-red
        data:
          - refId: A                                   # burn rate за 1 ч
            datasourceUid: mimir
            relativeTimeRange: { from: 3600, to: 0 }
            model:
              expr: |
                (1 - (
                  sum(rate(http_server_requests_seconds_count{application="esim-provisioning",uri="/sims/{id}/activate",status!~"5.."}[1h]))
                  / sum(rate(http_server_requests_seconds_count{application="esim-provisioning",uri="/sims/{id}/activate"}[1h]))
                )) / (1 - 0.999)
              instant: true
          - refId: B                                   # burn rate за 5 м тоже должен быть высоким (всё ещё происходит)
            datasourceUid: mimir
            relativeTimeRange: { from: 300, to: 0 }
            model:
              expr: |
                (1 - (
                  sum(rate(http_server_requests_seconds_count{application="esim-provisioning",uri="/sims/{id}/activate",status!~"5.."}[5m]))
                  / sum(rate(http_server_requests_seconds_count{application="esim-provisioning",uri="/sims/{id}/activate"}[5m]))
                )) / (1 - 0.999)
              instant: true
          - refId: C
            datasourceUid: __expr__
            model:
              type: math
              expression: "$A > 14.4 && $B > 14.4"
```

Правило уровня тикета: те же запросы с окнами `[6h]`/`[30m]` и порогом 6, `severity: ticket`. При окне в 30 дней burn 14.4, продержавшийся 1 ч, съедает 2 % месячного бюджета — это обычный порог, чтобы кого-то разбудить.

## Подводные камни
- SLO по метрике, которую сервис сообщает сам о себе: когда pod лежит, он ничего не сообщает, и отношение выглядит идеальным. Используй метрики шлюза или blackbox-пробу и настрой алерт на `absent()`.
- Средние значения как SLI (`avg latency < 200 ms`): один пользователь, ждущий 10 с, невидим. Используй отношения запросов ниже порога по гистограммам ([[otel-metric-types]]).
- Алерты на каждый перезапуск pod, каждую 5xx, каждую паузу GC: пейджер превращается в шум, и настоящий сбой пропускается. В пейджи повышай только то, у чего есть runbook и влияние на пользователей.
- SLO, который никто не просматривает, — это просто дашборд. Включи бюджет в sprint review.
- Связанные заметки: [[grafana-dashboards-and-red-use]], [[observability-three-pillars]], [[requirements-and-constraints]] (SLO как нефункциональные требования), [[failure-modes-and-timeouts]], [[leases-heartbeats-and-health]].
